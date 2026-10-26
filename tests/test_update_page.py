"""The panel's System > Update page (docs/FINDINGS.md 86), from the bottom up:

  * mu300-update's additions for a panel, sourced for their functions as test_update does: `status` and `releases`
    as JSON, an update from a file (a Magisk zip of a release, or a directory of release files) with what it refuses,
    and the signature of SHA256SUMS, made here with a throwaway Ed25519 key in usign's format;
  * the adapter (luci-app-mu300's unisoc-modem/update) against a stand-in mu300-update: what each operation needs
    before it starts anything, the one background task and its states, the upload's staging directory, the log as the
    page gets it;
  * the page itself under Node, with a small DOM and canned replies.

mu300dash's own checks of update_get/update_job/update_act (every hostile value in every field) are in
test_mu300dash_security, with the other methods."""
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import unittest
import zipfile
from pathlib import Path

from helpers import BIN, TOP, ShellTest
from test_mu300dash_security import JSONFILTER
import test_update
from test_update import UpdateBase

APP = TOP / 'openwrt' / 'luci-app-mu300'
ADAPTER = APP / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'update'
VIEW = APP / 'htdocs' / 'luci-static' / 'resources' / 'view' / 'mu300' / 'update.js'
COMMON = APP / 'htdocs' / 'luci-static' / 'resources' / 'mu300' / 'common.js'
UPDATE = BIN / 'mu300-update'


def openssl():
    """An openssl that makes and checks Ed25519 signatures of a file (OpenSSL 3; macOS's LibreSSL does not)."""
    for c in (shutil.which('openssl'), '/opt/homebrew/bin/openssl', '/usr/local/opt/openssl@3/bin/openssl',
              '/opt/homebrew/opt/openssl@3/bin/openssl'):
        if not c or not os.path.exists(c):
            continue
        r = subprocess.run([c, 'pkeyutl', '-help'], capture_output=True, text=True)
        if '-rawin' in r.stdout + r.stderr:
            return c
    return None


OPENSSL = openssl()


class Key:
    """A throwaway release key: the public key and signatures as usign and signify write them (a comment line, then
    base64 of "Ed", an 8-byte key number and the 32-byte key or the 64-byte signature)."""

    def __init__(self, d, number=b'\x01\x02\x03\x04\x05\x06\x07\x08'):
        self.d, self.number = Path(d), number
        self.d.mkdir(parents=True, exist_ok=True)
        self.pem = self.d / 'key.pem'
        subprocess.run([OPENSSL, 'genpkey', '-algorithm', 'ed25519', '-out', str(self.pem)], check=True, capture_output=True)
        der = subprocess.run([OPENSSL, 'pkey', '-in', str(self.pem), '-pubout', '-outform', 'DER'], check=True,
                             capture_output=True).stdout
        self.pub = self.d / 'release.pub'
        self.pub.write_text('untrusted comment: a test key\n' + base64.b64encode(b'Ed' + number + der[-32:]).decode() + '\n')

    def sign(self, path, out=None):
        raw = self.d / 'sig.raw'
        subprocess.run([OPENSSL, 'pkeyutl', '-sign', '-inkey', str(self.pem), '-rawin', '-in', str(path), '-out', str(raw)],
                       check=True, capture_output=True)
        out = Path(out) if out else Path(str(path) + '.sig')
        out.write_text('untrusted comment: signed by a test key\n' +
                       base64.b64encode(b'Ed' + self.number + raw.read_bytes()).decode() + '\n')
        return out


def tgz(path, files):
    """a tar.gz with ./NAME members, as the release's files are"""
    with tarfile.open(path, 'w:gz') as t:
        for name, data, mode in files:
            data = data.encode() if isinstance(data, str) else data
            ti = tarfile.TarInfo('./' + name)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


ROOTFS = {('openwrt', ''): 'mu300-openwrt-rootfs.tar.gz', ('openwrt-luci', ''): 'mu300-openwrt-luci-rootfs.tar.gz',
          ('ubuntu', '24.04'): 'mu300-ubuntu-rootfs.tar.gz', ('ubuntu', '26.04'): 'mu300-ubuntu-26.04-rootfs.tar.gz'}
KERNEL = {'5.4': 'mu300-kernel.tar.gz', '6.18': 'mu300-kernel-6.18.tar.gz', '7.2': 'mu300-kernel-7.2.tar.gz', '7.3': 'mu300-kernel-7.3.tar.gz'}


def release_dir(d, tag='v2026.11.02', os_='openwrt-luci', ubuntu='', kernel='5.4', devices='f50 u30air\n'):
    """a release's files for one system and one kernel, with their SHA256SUMS: the directory apply --from takes"""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    rootfs, kasset = ROOTFS[(os_, ubuntu)], KERNEL[kernel]
    tgz(d / rootfs, [('sbin/init', '#!/bin/sh\n', 0o755), ('etc/mu300/image-version', tag + '\n', 0o644),
                     ('etc/config/system', 'new\n', 0o644), ('opt/mu300/bin/marker', tag + '\n', 0o644)])
    files = [('features', 'sdcard\nlinux-slot\n', 0o644), ('Image', 'kernel', 0o644)]
    if devices is not None:
        files.append(('devices', devices, 0o644))
    tgz(d / kasset, files)
    # mu300-release: the tag as a file, listed like the others - what binds the checksums to their release
    (d / 'mu300-release').write_text(tag + '\n')
    (d / 'SHA256SUMS').write_text(''.join(f'{sha(d / n)}  {n}\n' for n in (kasset, rootfs, 'mu300-release')))
    return d


def magisk_zip(path, rel, tag='v2026.11.02', os_='openwrt-luci', ubuntu='', kernel='5.4', sums=False, sig=None,
               manifest=None, extra=()):
    """the Magisk zip of that release as tools/make-magisk-zips.sh makes it: mu300/manifest and the stored payload;
    SUMS adds the release's SHA256SUMS (what a signature covers), SIG its signature"""
    rel = Path(rel)
    rootfs, kasset = ROOTFS[(os_, ubuntu)], KERNEL[kernel]
    system = os_ + ('-' + ubuntu if ubuntu else '')
    m = (f'TAG={tag}\nSYSTEM={system}\nOS={os_}\nUBUNTU={ubuntu}\nKERNEL={kernel}\nKERNEL_ASSET={kasset}\n'
         f'ROOTFS_ASSET={rootfs}\nSHA256_KERNEL={sha(rel / kasset)}\nSHA256_ROOTFS={sha(rel / rootfs)}\n')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('mu300/manifest', m if manifest is None else manifest)
        z.writestr('module.prop', 'id=mu300_linux_switch\n')
        if sums:
            z.writestr('mu300/SHA256SUMS', (rel / 'SHA256SUMS').read_text())
        if sig:
            z.writestr('mu300/SHA256SUMS.sig', Path(sig).read_text())
        for n in (kasset, rootfs):
            z.write(rel / n, 'payload/' + n, zipfile.ZIP_STORED)
        for name, data in extra:
            z.writestr(name, data)
    return Path(path)


@unittest.skipUnless(shutil.which('unzip') or shutil.which('busybox'), 'unzip is needed to read a Magisk zip')
class Offline(UpdateBase):
    """apply --from: the release from a Magisk zip or a directory, and what is refused before anything changes."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.disk / 'ubuntu')
        old = self.disk / 'openwrt-luci'
        for f, data in [('sbin/init', '#!/bin/sh\n'), ('etc/mu300/image-version', 'v2026.10.25\n'),
                        ('etc/config/system', 'mine\n'), ('etc/mu300/hotspot.conf', 'mine\n')]:
            (old / f).parent.mkdir(parents=True, exist_ok=True)
            (old / f).write_text(data)
        self.rel = release_dir(self.tmp / 'rel')
        self.zip = magisk_zip(self.tmp / 'mu300-magisk-v2026.11.02-openwrt-luci-k5.4.zip', self.rel)
        self.nokey = self.tmp / 'no-release.pub'

    def frm(self, shell, src, code, **env):
        env.setdefault('MU300_RELEASE_PUB', self.nokey)
        return self.up(shell, f'MU300_FROM="{src}"; {code}', TMPDIR=self.tmp, **env)

    def inspect(self, shell, src, **env):
        env.setdefault('MU300_RELEASE_PUB', self.nokey)
        r = self.up(shell, f'inspect_json "{src}"', TMPDIR=self.tmp, **env)
        return json.loads(r.stdout), r

    def test_a_zip_says_what_it_is(self):
        for shell in self.each_shell():
            o, r = self.inspect(shell, self.zip)
            self.assertEqual(o, {'ok': 1, 'kind': 'zip', 'sha256': sha(self.zip), 'size': self.zip.stat().st_size,
                                 'tag': 'v2026.11.02', 'system': 'openwrt-luci', 'ubuntu': None, 'kernel': '5.4',
                                 'signature': 'nokey', 'installed': 'v2026.10.25', 'downgrade': 0, 'same': 0, 'fits': 1,
                                 'why': None}, r.stderr)
            # nothing is left behind, and nothing was unpacked onto the partition
            self.assertFalse((self.disk / '.mu300-update').exists())
            self.assertEqual([p.name for p in self.tmp.glob('tmp.*')], [])

    def test_what_is_not_for_this_device_says_why(self):
        other = release_dir(self.tmp / 'rel-ubuntu', os_='ubuntu', ubuntu='24.04')
        k72 = release_dir(self.tmp / 'rel-72', kernel='7.2')
        f50 = release_dir(self.tmp / 'rel-f50', devices='f50\n')
        old = release_dir(self.tmp / 'rel-olddev', devices=None)
        cases = [
            (magisk_zip(self.tmp / 'u.zip', other, os_='ubuntu', ubuntu='24.04'), 'f50', 'system-missing'),
            (magisk_zip(self.tmp / 'k.zip', k72, kernel='7.2'), 'f50', 'kernel'),
            (magisk_zip(self.tmp / 'd.zip', f50), 'u30air', 'device'),
            (magisk_zip(self.tmp / 'o.zip', old), 'u30air', 'device'),    # a bundle from before ./devices: the F50's
            (magisk_zip(self.tmp / 'o2.zip', old), 'f50', None),
            (magisk_zip(self.tmp / 'd2.zip', f50), 'f50', None),
        ]
        for shell in self.each_shell():
            for z, device, why in cases:
                self.device(device)
                o, r = self.inspect(shell, z)
                self.assertEqual((o['ok'], o['fits'], o['why']), (1, 0 if why else 1, why), (z.name, device, r.stderr))
                r = self.frm(shell, z, 'MU300_ALLOW_UNSIGNED=1; from_begin; echo began')
                self.assertEqual('began' in r.stdout, why is None, (z.name, r.stderr))
                if why:
                    self.assertIn('is not for this device', r.stderr)
            self.device('f50')
        # the other Ubuntu release than the one installed
        (self.disk / 'ubuntu/etc').mkdir(parents=True)
        (self.disk / 'ubuntu/etc/os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        for shell in self.each_shell():
            o, _ = self.inspect(shell, self.tmp / 'u.zip')
            self.assertEqual((o['fits'], o['why']), (0, 'ubuntu-release'))

    def test_what_is_not_a_release_is_refused(self):
        m = 'TAG={tag}\nSYSTEM=x\nOS={os}\nUBUNTU=\nKERNEL={k}\nKERNEL_ASSET={ka}\nROOTFS_ASSET={ra}\nSHA256_KERNEL={h}\nSHA256_ROOTFS={h}\n'
        good = dict(tag='v2026.11.02', os='openwrt-luci', k='5.4', ka='mu300-kernel.tar.gz',
                    ra='mu300-openwrt-luci-rootfs.tar.gz', h='a' * 64)
        bad = {
            'tag of a test build': dict(good, tag='test-issue-102'),
            'tag with a command': dict(good, tag='v2026.11.02;reboot'),
            'tag with a path': dict(good, tag='../../etc'),
            'unknown system': dict(good, os='android'),
            'unknown kernel': dict(good, k='6.6'),
            'a rootfs name of its own': dict(good, ra='../../etc/shadow'),
            'a rootfs of another system': dict(good, ra='mu300-ubuntu-rootfs.tar.gz'),
            'a kernel name of its own': dict(good, ka='payload/../x'),
            'a hash that is none': dict(good, h='$(reboot)'),
        }
        files = {n: magisk_zip(self.tmp / f'bad{i}.zip', self.rel, manifest=m.format(**v)) for i, (n, v) in enumerate(bad.items())}
        with zipfile.ZipFile(self.tmp / 'nomanifest.zip', 'w') as z:
            z.writestr('module.prop', 'id=x\n')
        files['a zip without a manifest'] = self.tmp / 'nomanifest.zip'
        (self.tmp / 'text.zip').write_text('PK but not a zip\n')
        files['a file that only starts like a zip'] = self.tmp / 'text.zip'
        (self.tmp / 'rootfs.tar.gz').write_bytes((self.rel / 'mu300-openwrt-luci-rootfs.tar.gz').read_bytes())
        files['a root filesystem alone'] = self.tmp / 'rootfs.tar.gz'
        (self.tmp / 'link.zip').symlink_to(self.zip)
        files['a link to a release'] = self.tmp / 'link.zip'
        files['nothing'] = self.tmp / 'none.zip'
        (self.tmp / 'emptydir').mkdir()
        files['a directory without SHA256SUMS'] = self.tmp / 'emptydir'
        for shell in self.each_shell():
            for name, f in files.items():
                with self.subTest(case=name):
                    o, r = self.inspect(shell, f)
                    self.assertEqual(o['ok'], 0, r.stdout)
                    self.assertIn(o['why'], ('not-release', 'not-found', 'no-tag'))
                    r = self.frm(shell, f, 'MU300_ALLOW_UNSIGNED=1; from_begin; echo began')
                    self.assertNotIn('began', r.stdout)
                    self.assertIn('is not a release of this project', r.stderr)
                    self.assertFalse((self.tmp / 'reboot').exists())
                    self.assertEqual([p.name for p in self.tmp.glob('tmp.*')], [])

    def test_a_damaged_file_is_seen_before_anything_is_applied(self):
        # the payload does not match the hashes the zip carries: the page says so, and apply stops at the file
        m = (f'TAG=v2026.11.02\nSYSTEM=openwrt-luci\nOS=openwrt-luci\nUBUNTU=\nKERNEL=5.4\nKERNEL_ASSET=mu300-kernel.tar.gz\n'
             f'ROOTFS_ASSET=mu300-openwrt-luci-rootfs.tar.gz\nSHA256_KERNEL={sha(self.rel / "mu300-kernel.tar.gz")}\n'
             f'SHA256_ROOTFS={"0" * 64}\n')
        z = magisk_zip(self.tmp / 'damaged.zip', self.rel, manifest=m)
        for shell in self.each_shell():
            o, _ = self.inspect(shell, z)
            self.assertEqual((o['ok'], o['fits'], o['why']), (1, 0, 'checksum'))
            r = self.frm(shell, z, 'MU300_ALLOW_UNSIGNED=1; from_begin; from_verified v2026.11.02 mu300-openwrt-luci-rootfs.tar.gz; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertIn('does not match its checksum', r.stderr)
            self.assertFalse((self.disk / '.mu300-update/mu300-openwrt-luci-rootfs.tar.gz').exists())

    def test_files_come_out_of_the_source_checked(self):
        for shell in self.each_shell():
            for src in (self.zip, self.rel):
                shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
                r = self.frm(shell, src, 'MU300_ALLOW_UNSIGNED=1; from_begin; for f in mu300-openwrt-luci-rootfs.tar.gz mu300-kernel.tar.gz '
                                         'mu300-extra-lang.tar.gz mu300-update; do fetch_verified v2026.11.02 $f; echo "$f=$?"; done')
                stage = self.disk / '.mu300-update'
                self.assertIn('mu300-openwrt-luci-rootfs.tar.gz=0', r.stdout, r.stderr)
                self.assertIn('mu300-kernel.tar.gz=0', r.stdout)
                # what the source does not have is "not part of this release" (2), as on GitHub: the installed
                # language pack stays, and the run goes on with this updater
                self.assertIn('mu300-extra-lang.tar.gz=2', r.stdout)
                self.assertIn('mu300-update=2', r.stdout)
                self.assertEqual(sha(stage / 'mu300-kernel.tar.gz'), sha(self.rel / 'mu300-kernel.tar.gz'))
                self.assertEqual((stage / 'SHA256SUMS.tag').read_text(), 'v2026.11.02\n')
                # the staging directory is closed to everyone else before anything is checked in it
                self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            # a source that was not judged first gives nothing
            shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
            r = self.frm(shell, self.zip, 'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertIn('was not checked', r.stderr)

    def test_checksums_swapped_after_the_check_stop_the_run(self):
        # time of check, time of use: the checksums are judged once and pinned by their hash; other bytes at their
        # place afterwards (and a payload that matches those) are not taken
        evil = release_dir(self.tmp / 'evil')
        tgz(evil / 'mu300-kernel.tar.gz', [('Image', 'another kernel', 0o644)])
        swapped = f'{sha(evil / "mu300-kernel.tar.gz")}  mu300-kernel.tar.gz\n'
        for shell in self.each_shell():
            shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
            r = self.frm(shell, self.zip, 'MU300_ALLOW_UNSIGNED=1; from_begin; fetch_verified v2026.11.02 mu300-openwrt-luci-rootfs.tar.gz; '
                                          f'echo "first=$?"; printf "%s" "{swapped}" > "$FROM_TMP/SHA256SUMS"; '
                                          f'cp "{evil}/mu300-kernel.tar.gz" "$STAGE/mu300-kernel.tar.gz"; '
                                          'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "second=$?"')
            self.assertIn('first=0', r.stdout, r.stderr)
            self.assertIn('second=1', r.stdout)
            self.assertIn('changed after they were checked', r.stderr)

    def test_what_is_staged_is_roots_alone(self):
        # the staging directory 700 and every file this puts there 600, whatever the umask of the run - which stays
        # what it was for the rest of the update (the new system's directories are made under it)
        for shell in self.each_shell():
            shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
            r = self.frm(shell, self.zip, 'umask 022; MU300_ALLOW_UNSIGNED=1; from_begin; fetch_verified v2026.11.02 mu300-kernel.tar.gz; '
                                          'echo "umask=$(umask)"')
            self.assertIn('umask=0022', r.stdout.replace('umask=022', 'umask=0022'), r.stderr)
            stage = self.disk / '.mu300-update'
            self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            open_ = {str(p.relative_to(stage)): oct(p.stat().st_mode & 0o777) for p in stage.rglob('*') if p.stat().st_mode & 0o077}
            self.assertEqual(open_, {})
            self.assertTrue((stage / 'mu300-kernel.tar.gz').exists() and (stage / '.from/SHA256SUMS').exists())
            # one that others could write into is emptied before it is used: what they left there is not followed
            shutil.rmtree(stage)
            stage.mkdir()
            stage.chmod(0o777)
            victim = self.tmp / 'victim'
            victim.write_text('untouched')
            (stage / 'SHA256SUMS').symlink_to(victim)
            (stage / 'mu300-kernel.tar.gz').symlink_to(victim)
            r = self.frm(shell, self.zip, 'MU300_ALLOW_UNSIGNED=1; from_begin; fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "rc=$?"')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(victim.read_text(), 'untouched')
            self.assertEqual(stage.stat().st_mode & 0o777, 0o700)
            self.assertFalse((stage / 'mu300-kernel.tar.gz').is_symlink())

    def test_the_file_that_was_looked_at_is_the_file_that_is_used(self):
        # the page shows a file's SHA-256 and the owner says yes to that file: the updater takes it by that hash
        other = magisk_zip(self.tmp / 'other.zip', release_dir(self.tmp / 'rel2', tag='v2026.11.03'), tag='v2026.11.03')
        for shell in self.each_shell():
            r = self.frm(shell, self.zip, f'apply_options --from "{self.zip}" --unsigned --sha256={sha(self.zip)}; from_begin; echo "began $MU300_RELEASE"')
            self.assertIn('began v2026.11.02', r.stdout, r.stderr)
            # another file at that path since
            r = self.frm(shell, other, f'apply_options --from "{other}" --unsigned --sha256={sha(self.zip)}; from_begin; echo began')
            self.assertNotIn('began', r.stdout)
            self.assertIn('is not the file that was looked at', r.stderr)
            for bad in ('--sha256=xyz', '--sha256=', '--sha256=' + 'A' * 64):
                r = self.up(shell, f'apply_options --from /x.zip {bad}; echo parsed')
                self.assertNotIn('parsed', r.stdout, bad)
            # the hash is this run's own word, not the environment's
            r = self.up(shell, 'apply_options --from /x.zip; echo "[$MU300_FROM_SHA]"', MU300_FROM_SHA='ab' * 32)
            self.assertEqual(r.stdout.strip(), '[]')

    def test_a_system_under_another_releases_name_is_not_installed(self):
        # what is judged (the signature, the downgrade rule, what the page showed) is a name; what is installed is
        # the files. An unsigned zip that calls itself v2026.12.24 and carries the system of v2026.09.01 passes the
        # downgrade rule by its name - and stops at the root filesystem's own word, before anything is changed.
        old = release_dir(self.tmp / 'old', tag='v2026.09.01')
        liar = magisk_zip(self.tmp / 'liar.zip', old, tag='v2026.12.24')
        nameless = release_dir(self.tmp / 'nameless')
        tgz(nameless / 'mu300-openwrt-luci-rootfs.tar.gz', [('sbin/init', '#!/bin/sh\n', 0o755)])
        (nameless / 'SHA256SUMS').write_text(''.join(f'{sha(nameless / n)}  {n}\n' for n in ('mu300-kernel.tar.gz', 'mu300-openwrt-luci-rootfs.tar.gz')))
        for shell in self.each_shell():
            for src, said, env in ((liar, 'says it is v2026.09.01, not v2026.12.24', {}),
                                   (nameless, 'says it is no release at all, not v2026.11.02', {'MU300_RELEASE': 'v2026.11.02'})):
                shutil.rmtree(self.disk / 'openwrt-luci.old', ignore_errors=True)
                shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
                r = self.frm(shell, src, self.APPLY + 'MU300_ALLOW_UNSIGNED=1; apply', **env, **self.busybox_applets(shell))
                self.assertNotEqual(r.returncode, 0, src.name)
                self.assertIn(said, r.stderr)
                self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.25\n')
                self.assertFalse((self.disk / 'openwrt-luci.old').exists())
                self.assertNotIn('BOOT', r.stdout)
            # the same for a download: a mirror that serves the old system as the new release (no key on the system)
            shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
            serve = (f'fetch_stdout() {{ cat "{old}/${{1##*/}}" 2>/dev/null; }}; fetch_file() {{ cp "{old}/${{1##*/}}" "$2" 2>/dev/null; }}; sleep() {{ :; }}; ')
            (old / 'SHA256SUMS').write_text(''.join(l + '\n' for l in (old / 'SHA256SUMS').read_text().split('\n') if l and 'mu300-release' not in l))
            r = self.up(shell, self.APPLY + serve + 'apply', MU300_RELEASE='v2026.12.24', MU300_RELEASE_PUB=self.nokey, **self.busybox_applets(shell))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('says it is v2026.09.01, not v2026.12.24', r.stderr)
            self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.25\n')

    def test_the_boot_image_is_not_taken_back_by_a_system_update(self):
        # an update writes the release's kernel too: a system that was rolled back is older than the boot image, and
        # a release between the two would take the boot image back without a word
        (self.disk / 'boot').mkdir()
        (self.disk / 'boot/installed.tag').write_text('v2026.12.01\n')
        for shell in self.each_shell():
            r = self.up(shell, 'downgrade_ok v2026.11.02 openwrt-luci; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertIn('the boot image is v2026.12.01 and v2026.11.02 is older', r.stderr)
            self.assertIn('rc=0', self.up(shell, 'downgrade_ok v2026.12.01 openwrt-luci; echo "rc=$?"').stdout)
            self.assertIn('rc=0', self.up(shell, 'downgrade_ok v2026.11.02 openwrt-luci; echo "rc=$?"', MU300_ALLOW_DOWNGRADE=1).stdout)

    def test_a_staging_directory_that_is_not_ours_is_not_used(self):
        elsewhere = self.tmp / 'elsewhere'
        elsewhere.mkdir()
        (self.disk / '.mu300-update').symlink_to(elsewhere)
        for shell in self.each_shell():
            r = self.frm(shell, self.zip, 'MU300_ALLOW_UNSIGNED=1; from_begin; echo began')
            self.assertNotIn('began', r.stdout)
            r = self.up(shell, 'fetch_stdout() { echo x; }; fetch_verified v2026.11.02 mu300-update; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertIn('symbolic link', r.stderr)
            self.assertEqual(os.listdir(elsewhere), [])

    def test_an_older_release_is_never_taken_by_itself(self):
        # whatever the source - GitHub, a mirror (MU300_RELEASE, MU300_RELEASE_URL), a file - and before the
        # release's own updater is given the run: an old release is a way back to its faults
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.12.01\n')
        asked = 'fetch_stdout() { echo "FETCHED $1" >&2; return 1; }; fetch_file() { echo "FETCHED $1" >&2; return 1; }; '
        for shell in self.each_shell():
            for env in ({'MU300_RELEASE': 'v2026.11.02'}, {'MU300_RELEASE': 'v2026.11.02', 'MU300_RELEASE_URL': 'http://mirror.invalid/x'}):
                r = self.up(shell, self.APPLY + asked + 'apply', **env)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn('openwrt-luci is v2026.12.01 and v2026.11.02 is older', r.stderr)
                self.assertNotIn('FETCHED', r.stderr)             # not even the release's updater was asked for
                self.assertNotIn('BOOT', r.stdout)
                # said in words, it goes on (and here stops at the download)
                r = self.up(shell, self.APPLY + asked + 'apply_options --downgrade; apply $APPLY_ARGS', **env)
                self.assertIn('FETCHED', r.stderr)
            # the newest release as "latest" says it, older than a test release that is installed
            r = self.up(shell, self.APPLY + asked + 'latest_release() { echo v2026.11.02; }; apply')
            self.assertIn('is older', r.stderr)
            # the same release, or a newer one, needs no word
            r = self.up(shell, self.APPLY + asked + 'apply', MU300_RELEASE='v2026.12.01')
            self.assertNotIn('is older', r.stderr)
            # a kernel or a boot image taken alone
            for code in ('boot_downgrade_ok v2026.11.02; echo "rc=$?"',):
                r = self.up(shell, 'installed_version() { echo v2026.12.01; }; ' + code)
                self.assertIn('rc=1', r.stdout)
                r = self.up(shell, 'installed_version() { echo v2026.12.01; }; ' + code, MU300_ALLOW_DOWNGRADE=1)
                self.assertIn('rc=0', r.stdout)
            # the new names (FINDINGS 125): every vYYYY.MM.DD.N is newer than every vYYYY.MM.DD, whatever its date
            (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.27\n')
            r = self.up(shell, self.APPLY + asked + 'apply', MU300_RELEASE='v2026.10.16.1')
            self.assertNotIn('is older', r.stderr)
            (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.16.10\n')
            for older in ('v2026.10.27', 'v2026.10.16.9'):
                r = self.up(shell, self.APPLY + asked + 'apply', MU300_RELEASE=older)
                self.assertIn(f'openwrt-luci is v2026.10.16.10 and {older} is older', r.stderr)
            (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.12.01\n')
            src = UPDATE.read_text()
            self.assertLess(src.index('downgrade_ok "$tag" $(apply_targets "$@")'), src.index('exec sh "$STAGE/mu300-update" apply'))
            self.assertIn('boot_downgrade_ok "$t" || exit 1', src)
            self.assertIn('boot_downgrade_ok "$t" || { echo "$old" > "$KERNEL_CHOICE"', src)

    def test_a_directory_names_its_release_by_its_root_filesystem(self):
        for shell in self.each_shell():
            o, r = self.inspect(shell, self.rel)
            self.assertEqual((o['ok'], o['kind'], o['tag'], o['fits'], o['sha256']), (1, 'dir', 'v2026.11.02', 1, None), r.stderr)
            o, _ = self.inspect(shell, self.rel, MU300_RELEASE='v2026.11.09')
            self.assertEqual(o['tag'], 'v2026.11.09')

    def test_an_older_release_needs_the_word(self):
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.12.01\n')
        for shell in self.each_shell():
            o, _ = self.inspect(shell, self.zip)
            self.assertEqual((o['downgrade'], o['installed'], o['fits']), (1, 'v2026.12.01', 1))
            r = self.frm(shell, self.zip, 'MU300_ALLOW_UNSIGNED=1; from_begin; echo began')
            self.assertNotIn('began', r.stdout)
            self.assertIn('older than what is installed (v2026.12.01)', r.stderr)
            r = self.frm(shell, self.zip, 'MU300_ALLOW_UNSIGNED=1; MU300_ALLOW_DOWNGRADE=1; from_begin; echo "began $MU300_RELEASE"')
            self.assertIn('began v2026.11.02', r.stdout, r.stderr)

    def test_a_file_nothing_vouches_for_needs_the_word(self):
        # no key on this system, or no signature in the file: an upload has no origin, so it is never taken silently
        for shell in self.each_shell():
            r = self.frm(shell, self.zip, 'from_begin; echo began')
            self.assertNotIn('began', r.stdout)
            self.assertIn('is not signed with the project\'s release key', r.stderr)
            self.assertIn('nothing was changed', r.stderr)
            r = self.frm(shell, self.zip, 'MU300_ALLOW_UNSIGNED=1; from_begin; echo "began $FROM_ONLY"')
            self.assertIn('began openwrt-luci', r.stdout, r.stderr)
            self.assertIn('used because you said so', r.stderr)

    def test_apply_options(self):
        for shell in self.each_shell():
            r = self.up(shell, 'apply_options --no-reinstall --from /x/y.zip openwrt --unsigned --downgrade; '
                               'echo "[$MU300_FROM][$MU300_ALLOW_UNSIGNED][$MU300_ALLOW_DOWNGRADE][$APPLY_ARGS]"')
            self.assertEqual(r.stdout.strip(), '[/x/y.zip][1][1][ --no-reinstall openwrt]', r.stderr)
            # the owner's word reaches the release's own updater, which apply hands over to with the options taken
            # out (on the U30 Air "apply --unsigned" was refused by the next release's updater)
            r = self.up(shell, 'apply_options --unsigned --downgrade; '
                               'sh -c \'echo "[$MU300_ALLOW_UNSIGNED][$MU300_ALLOW_DOWNGRADE]"\'')
            self.assertEqual(r.stdout.strip(), '[1][1]', r.stderr)
            r = self.up(shell, 'apply_options ubuntu; echo "[${MU300_FROM:-}][${MU300_ALLOW_UNSIGNED:-}][$APPLY_ARGS]"')
            self.assertEqual(r.stdout.strip(), '[][][ ubuntu]')
            for bad in ('--from', 'openwrt "*"', '"a b"', '--from x "$(echo y)z;"'):
                r = self.up(shell, f'apply_options {bad}; echo parsed')
                self.assertNotIn('parsed', r.stdout, bad)
                self.assertNotEqual(r.returncode, 0)

    APPLY = ('boot_update() { echo "BOOT $1 $(kernel_asset)"; [ -s "$STAGE/$(kernel_asset)" ] || echo "NO BUNDLE"; }; '
             'pause_data() { :; }; resume_data() { :; }; modules_fetch() { :; }; modules_install() { :; }; '
             'modules_link() { :; }; extras_install() { :; }; is_root() { false; }; mounted_disk() { :; }; '
             'df() { printf "Filesystem 1K-blocks Used Available Use%% Mounted\\n/dev/x 9 9 9999999 1%% /\\n"; }; '
             'latest_release() { echo "ASKED GITHUB" >&2; }; ')

    def test_the_whole_update_from_a_zip(self):
        self.stub('curl', 'echo "network used" >> "$STUBLOG/net"; exit 7')
        self.stub('wget', 'echo "network used" >> "$STUBLOG/net"; exit 7')
        for shell in self.each_shell():
            for d in ('openwrt-luci.old', '.mu300-update'):
                shutil.rmtree(self.disk / d, ignore_errors=True)
            (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
            (self.disk / 'openwrt-luci/etc/config/system').write_text('mine\n')
            r = self.frm(shell, self.zip, self.APPLY + 'MU300_ALLOW_UNSIGNED=1; apply_options --no-reinstall; apply $APPLY_ARGS',
                         MU300_DEFAULT_BOOT=self.tmp / 'default-boot', **self.busybox_applets(shell))
            self.assertEqual(r.returncode, 0, r.stderr)
            new = self.disk / 'openwrt-luci'
            self.assertEqual((new / 'etc/mu300/image-version').read_text(), 'v2026.11.02\n', r.stdout + r.stderr)
            self.assertEqual((new / 'opt/mu300/bin/marker').read_text(), 'v2026.11.02\n')
            self.assertEqual((new / 'etc/config/system').read_text(), 'mine\n')
            self.assertEqual((new / 'etc/mu300/hotspot.conf').read_text(), 'mine\n')
            self.assertEqual((self.disk / 'openwrt-luci.old/etc/mu300/image-version').read_text(), 'v2026.10.25\n')
            self.assertIn('BOOT v2026.11.02 mu300-kernel.tar.gz', r.stdout)
            self.assertNotIn('NO BUNDLE', r.stdout)
            # the release was the file's, and the network was never asked for it
            self.assertNotIn('ASKED GITHUB', r.stderr)
            self.assertFalse((self.tmp / 'net').exists())
            self.assertFalse((self.disk / '.mu300-update').exists())
            self.assertTrue(self.zip.exists())

    def test_a_refused_file_changes_nothing(self):
        for shell in self.each_shell():
            shutil.rmtree(self.disk / 'openwrt-luci.old', ignore_errors=True)
            (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
            r = self.frm(shell, self.zip, self.APPLY + 'apply', **self.busybox_applets(shell))
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.25\n')
            self.assertFalse((self.disk / 'openwrt-luci.old').exists())
            self.assertNotIn('BOOT', r.stdout)

    def test_the_run_goes_on_with_an_updater_that_knows_a_file_only(self):
        # apply execs the release's own mu300-update when it differs: one from before --from would ask GitHub
        old = self.tmp / 'old-updater'
        old.write_text('#!/bin/sh\nfetch_verified() { :; }\n')
        for shell in self.each_shell():
            r = self.frm(shell, self.zip, f'from_open; from_handover "{old}"; echo "old=$?"; from_handover "{UPDATE}"; '
                                          'echo "new=$? [$FROM_KIND]"')
            self.assertIn('old=1', r.stdout, r.stderr)
            self.assertIn('new=0 []', r.stdout)
            r = self.up(shell, f'from_handover "{old}"; echo "online=$?"')
            self.assertIn('online=0', r.stdout)


@unittest.skipUnless(OPENSSL, 'an openssl with Ed25519 (OpenSSL 3) is needed to make a test key')
class Signature(UpdateBase):
    """SHA256SUMS.sig against the release key of the system: the key is made here and thrown away."""

    def setUp(self):
        super().setUp()
        self.key = Key(self.tmp / 'key')
        self.other = Key(self.tmp / 'other', number=b'\x09' * 8)
        self.stub('openssl', f'exec "{OPENSSL}" "$@"')
        self.sums = self.tmp / 'SHA256SUMS'
        self.sums.write_text('a' * 64 + '  mu300-kernel.tar.gz\n')
        self.sig = self.key.sign(self.sums)

    def state(self, shell, sums, sig, pub=None, **env):
        r = self.up(shell, f'sig_state "{sums}" "{sig}"', MU300_RELEASE_PUB=pub or self.key.pub, TMPDIR=self.tmp, **env)
        return r.stdout.strip()

    def test_states(self):
        changed = self.tmp / 'changed'
        changed.write_text('b' * 64 + '  mu300-kernel.tar.gz\n')
        same_number = Key(self.tmp / 'same', number=self.key.number)
        garbage = self.tmp / 'garbage.sig'
        garbage.write_text('untrusted comment: x\nnot base64 at all !!\n')
        short = self.tmp / 'short.sig'
        short.write_text('untrusted comment: x\n' + base64.b64encode(b'Ed' + self.key.number + b'x' * 10).decode() + '\n')
        for shell in self.each_shell():
            self.assertEqual(self.state(shell, self.sums, self.sig), 'valid')
            self.assertEqual(self.state(shell, changed, self.sig), 'bad')                      # the file was changed
            self.assertEqual(self.state(shell, self.sums, self.other.sign(self.sums, self.tmp / 'o.sig')), 'bad')
            self.assertEqual(self.state(shell, self.sums, same_number.sign(self.sums, self.tmp / 's.sig')), 'bad')
            self.assertEqual(self.state(shell, self.sums, garbage), 'bad')
            self.assertEqual(self.state(shell, self.sums, short), 'bad')
            self.assertEqual(self.state(shell, self.sums, self.tmp / 'none.sig'), 'unsigned')
            self.assertEqual(self.state(shell, self.sums, self.sig, pub=self.tmp / 'no.pub'), 'nokey')
            # nothing was left in the temporary directory
            self.assertEqual([p.name for p in self.tmp.glob('tmp.*')], [])

    # What OpenWrt's usign itself wrote (usign -G, usign -S of the 11 bytes "abc  file1\n", in the 25.12 image; the
    # secret half was thrown away): the public key and the signature, to check that the openssl path reads them
    USIGN_PUB = 'untrusted comment: throwaway lab key\nRWRSqAkNKsFhk7kzU6iHnD8fkoPRhtR3ZZ/KrtsgyhRy8WNrOdUwT/5f\n'
    USIGN_SIG = ('untrusted comment: signed by key 52a8090d2ac16193\n'
                 'RWRSqAkNKsFhkz+TnEhFInvngZVj2+jIBlxdilrIgGI3qMLertumsyhiI8QvJ0EHjhgBfploxlZM7krZBNPohi0o6UH/0HuX5AE=\n')

    def test_a_signature_usign_made_is_read_without_usign(self):
        pub, sig, msg = self.tmp / 'usign.pub', self.tmp / 'usign.sig', self.tmp / 'usign.msg'
        pub.write_text(self.USIGN_PUB)
        sig.write_text(self.USIGN_SIG)
        for shell in self.each_shell():
            msg.write_text('abc  file1\n')
            self.assertEqual(self.state(shell, msg, sig, pub=pub), 'valid')
            msg.write_text('abc  file2\n')
            self.assertEqual(self.state(shell, msg, sig, pub=pub), 'bad')

    def secret(self, key, rounds=0):
        """KEY as the secret key file usign -G writes: "Ed", "BK", the passphrase rounds, a salt, a checksum, the
        key's number, the seed and the public key"""
        der = subprocess.run([OPENSSL, 'pkey', '-in', str(key.pem), '-outform', 'DER'], check=True, capture_output=True).stdout
        pub = base64.b64decode(key.pub.read_text().split('\n')[1])[10:]
        raw = b'EdBK' + rounds.to_bytes(4, 'big') + b'\x00' * 16 + b'\x00' * 8 + key.number + der[-32:] + pub
        return 'untrusted comment: a test secret key\n' + base64.b64encode(raw).decode() + '\n'

    def sign_release(self, sums, pub=None, **env):
        args = ['sh', str(TOP / 'tools/sign-release.sh'), str(sums)] + ([str(pub)] if pub else [])
        return subprocess.run(args, capture_output=True, text=True, env=self.env(MU300_OPENSSL=OPENSSL, TMPDIR=self.tmp, **env))

    def test_the_release_is_signed_as_the_devices_check_it(self):
        # tools/sign-release.sh, what make-release.sh runs with the repository's secret: its signature is the one the
        # updater accepts, and a key that is not the images' stops the release
        text = self.secret(self.key)
        seed = text.split('\n')[1]
        r = self.sign_release(self.sums, self.key.pub, MU300_RELEASE_KEY=text)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(seed, r.stdout + r.stderr)
        made = Path(str(self.sums) + '.sig').read_text()
        self.assertEqual(made.split('\n')[1], self.sig.read_text().split('\n')[1])     # Ed25519: one signature per key and file
        for shell in self.each_shell():
            self.assertEqual(self.state(shell, self.sums, str(self.sums) + '.sig'), 'valid')
        # from a file, and without a public key to check against
        f = self.tmp / 'release.sec'
        f.write_text(text)
        Path(str(self.sums) + '.sig').unlink()
        self.assertEqual(self.sign_release(self.sums, MU300_RELEASE_KEY_FILE=f).returncode, 0)
        self.assertEqual(Path(str(self.sums) + '.sig').read_text(), made)
        # refused, and no signature written: another key than the images', a key with a passphrase, no key, garbage
        for pub, env, says in ((self.other.pub, {'MU300_RELEASE_KEY': text}, 'is not the one'),
                               (self.key.pub, {'MU300_RELEASE_KEY': self.secret(self.key, rounds=42)}, 'passphrase'),
                               (self.key.pub, {}, 'no key'),
                               (self.key.pub, {'MU300_RELEASE_KEY': 'untrusted comment: x\nnot a key\n'}, 'not a usign secret key'),
                               (self.key.pub, {'MU300_RELEASE_KEY': self.secret(self.other)}, 'is not the one')):
            Path(str(self.sums) + '.sig').unlink(missing_ok=True)
            r = self.sign_release(self.sums, pub, **env)
            self.assertNotEqual(r.returncode, 0, says)
            self.assertIn(says, r.stderr)
            self.assertFalse(Path(str(self.sums) + '.sig').exists(), says)
            self.assertNotIn(seed, r.stdout + r.stderr)
        # nothing of the key is left in the temporary directory
        self.assertEqual([p.name for p in self.tmp.glob('tmp.*')], [])

    def test_the_release_scripts_sign_and_never_publish_unsigned_what_devices_would_refuse(self):
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('sh "$TOP/tools/sign-release.sh" "$D/SHA256SUMS" "$PUB"', rel)
        self.assertIn('[ "$PUBLISH" != --publish ] || exit 1', rel)
        self.assertEqual(rel.count('"$D/SHA256SUMS" $SIG'), 2)          # a new release, and files added to one
        wf = (TOP / '.github/workflows/release.yml').read_text()
        self.assertIn('MU300_RELEASE_KEY: ${{ secrets.MU300_RELEASE_KEY }}', wf)
        # the key is in no file of the tree: the public half arrives with the owner's commit, the secret never
        self.assertNotIn('RWRSqAkN', rel + wf)
        self.assertIn("RELEASE_PUB=${MU300_RELEASE_PUB:-/opt/mu300/lib/release.pub}", UPDATE.read_text())
        # the release names itself inside what is signed
        self.assertIn('echo "$TAG" > "$D/mu300-release"', rel)
        self.assertIn('sha256sum *.tar.gz *.manifest.gz mu300-update mu300-release; } > SHA256SUMS', rel)      # (and the manifests)
        self.assertEqual(rel.count('"$D/mu300-release" "$D/SHA256SUMS" $SIG'), 2)

    @unittest.skipUnless(shutil.which('zip') and shutil.which('unzip'), 'zip and unzip are needed to build the Magisk zips')
    def test_the_zips_of_a_signed_release_check_on_the_device(self):
        # the whole chain: the release is signed, the zip builder puts SHA256SUMS and its signature into each zip,
        # and the updater reads them out of the zip and says the file is the project's
        from test_magisk_zip import fake_release
        rel = self.tmp / 'v2026.11.02'
        fake_release(rel)
        with open(rel / 'SHA256SUMS', 'a') as f:        # as make-release.sh writes it: the release names itself
            f.write(hashlib.sha256(b'v2026.11.02\n').hexdigest() + '  mu300-release\n')
        self.assertEqual(self.sign_release(rel / 'SHA256SUMS', self.key.pub, MU300_RELEASE_KEY=self.secret(self.key)).returncode, 0)
        r = subprocess.run(['sh', str(TOP / 'tools/make-magisk-zips.sh'), str(rel), str(self.tmp / 'zips')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        z = self.tmp / 'zips/mu300-magisk-v2026.11.02-openwrt-luci-k5.4.zip'
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
        for shell in self.each_shell():
            r = self.up(shell, f'inspect_json "{z}"', MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            o = json.loads(r.stdout)
            self.assertEqual((o['ok'], o['tag'], o['system'], o['kernel'], o['signature'], o['fits']),
                             (1, 'v2026.11.02', 'openwrt-luci', '5.4', 'valid', 1), r.stderr)
            # and with another project's key in the system it is not this project's
            r = self.up(shell, f'inspect_json "{z}"', MU300_RELEASE_PUB=self.other.pub, TMPDIR=self.tmp)
            self.assertEqual(json.loads(r.stdout)['signature'], 'bad')

    def test_usign_does_it_where_there_is_one(self):
        # OpenWrt: usign -V -m SUMS -p KEY -x SIG, and its status is the answer
        self.stub('usign', 'printf "%s\\n" "$*" >> "$STUBLOG/usign"; [ -z "$USIGN_FAIL" ]')
        for shell in self.each_shell():
            self.assertEqual(self.state(shell, self.sums, self.sig), 'valid')
            self.assertEqual(self.state(shell, self.sums, self.sig, USIGN_FAIL=1), 'bad')
            self.assertIn(f'-V -q -m {self.sums} -p {self.key.pub} -x {self.sig}', (self.tmp / 'usign').read_text())

    def test_what_a_state_means(self):
        # the checksums of v2026.11.02 (they say so: their mu300-release line), of no release, and of another one
        named, plain, other = self.tmp / 'named', self.tmp / 'plain', self.tmp / 'other-release'
        tagsha = hashlib.sha256(b'v2026.11.02\n').hexdigest()
        named.write_text('a' * 64 + '  mu300-kernel.tar.gz\n' + tagsha + '  mu300-release\n')
        plain.write_text('a' * 64 + '  mu300-kernel.tar.gz\n')
        other.write_text('a' * 64 + '  mu300-kernel.tar.gz\n' + hashlib.sha256(b'v2026.10.01\n').hexdigest() + '  mu300-release\n')
        # (state, checksums, offline source, the owner's word) -> taken or not
        cases = [
            ('valid', named, '', '', True), ('valid', named, '/x.zip', '', True),
            # a signature says nothing about checksums that name no release, or another one - with any word
            ('valid', plain, '', '1', False), ('valid', other, '', '1', False), ('valid', other, '/x.zip', '1', False),
            ('unsigned', other, '/x.zip', '1', False), ('nokey', other, '', '', False),
            ('bad', named, '', '1', False), ('bad', named, '/x.zip', '1', False),
            ('badkey', named, '', '1', False), ('badkey', named, '/x.zip', '1', False),
            ('nokey', named, '', '', True), ('nokey', plain, '', '', True),    # online, no key: as it always was
            ('nokey', named, '/x.zip', '', False), ('nokey', plain, '/x.zip', '1', True),
            ('unsigned', named, '/x.zip', '', False), ('unsigned', plain, '/x.zip', '1', True),
            # this system has a key: no unsigned release silently, newer or older - taking the signature away is
            # not a way in
            ('unsigned', named, '', '', False), ('unsigned', plain, '', '', False), ('unsigned', plain, '', '1', True),
            ('notool', named, '', '', False), ('notool', named, '', '1', True),
        ]
        for shell in self.each_shell():
            for installed in ('v2026.10.25', 'v2026.12.01'):
                for state, sums, frm, word, taken in cases:
                    r = self.up(shell, f'installed_version() {{ echo {installed}; }}; MU300_FROM={frm}; MU300_ALLOW_UNSIGNED={word}; '
                                       f'sums_verdict v2026.11.02 {state} "release v2026.11.02" "{sums}"; echo "rc=$?"')
                    self.assertIn('rc=0' if taken else 'rc=1', r.stdout, (state, sums.name, frm, word, r.stderr))

    def test_the_key_is_the_systems_own_file_or_nothing_is_trusted(self):
        # never a key that came with the release, and never one that somebody else could have put there: a link, a
        # file others can write. That is not "no key" (which would let everything through as before signing)
        rel = release_dir(self.tmp / 'rel')
        sig = self.key.sign(rel / 'SHA256SUMS')
        z = magisk_zip(self.tmp / 'signed.zip', rel, sums=True, sig=sig)
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
        link = self.tmp / 'link.pub'
        link.symlink_to(self.key.pub)
        loose = self.tmp / 'loose.pub'
        loose.write_text(self.key.pub.read_text())
        loose.chmod(0o666)
        empty = self.tmp / 'empty.pub'
        empty.write_text('')
        get = (self.serve(rel) + 'installed_version() { echo v2026.10.25; }; '
               'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "rc=$?"')
        for shell in self.each_shell():
            for pub in (link, loose, empty, self.tmp):
                self.assertEqual(self.state(shell, rel / 'SHA256SUMS', sig, pub=pub), 'badkey', pub.name)
                shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
                for word in ('', '1'):
                    r = self.up(shell, get, MU300_RELEASE_PUB=pub, MU300_ALLOW_UNSIGNED=word, TMPDIR=self.tmp)
                    self.assertIn('rc=3', r.stdout, (pub.name, r.stderr))
                    r = self.up(shell, f'MU300_FROM="{z}"; from_begin; echo began', MU300_RELEASE_PUB=pub,
                                MU300_ALLOW_UNSIGNED=word, TMPDIR=self.tmp)
                    self.assertNotIn('began', r.stdout, pub.name)
                o = json.loads(self.up(shell, f'inspect_json "{z}"', MU300_RELEASE_PUB=pub, TMPDIR=self.tmp).stdout)
                self.assertEqual((o['signature'], o['fits'], o['why']), ('bad', 0, 'signature'), pub.name)
        # a zip that brings a key of its own, and is signed with it: the key is never read from there
        own = magisk_zip(self.tmp / 'own.zip', rel, sums=True, sig=self.other.sign(rel / 'SHA256SUMS', self.tmp / 'own.sig'),
                         extra=[('mu300/release.pub', self.other.pub.read_text()), ('release.pub', self.other.pub.read_text()),
                                ('opt/mu300/lib/release.pub', self.other.pub.read_text())])
        for shell in self.each_shell():
            o = json.loads(self.up(shell, f'inspect_json "{own}"', MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp).stdout)
            self.assertEqual(o['signature'], 'bad')
            o = json.loads(self.up(shell, f'inspect_json "{own}"', MU300_RELEASE_PUB=self.tmp / 'none.pub', TMPDIR=self.tmp).stdout)
            self.assertEqual(o['signature'], 'nokey')
        src = UPDATE.read_text()
        self.assertEqual(src.count('release.pub'), 1)          # one place names the key: the system's own path

    def test_a_signature_of_one_release_under_the_name_of_another(self):
        # the signed checksums of the old v2026.10.01, handed out as v2026.11.02: by a mirror, and in a zip whose
        # manifest says the newer tag. The tag is inside what is signed.
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
        old = release_dir(self.tmp / 'old', tag='v2026.10.01')
        sig = self.key.sign(old / 'SHA256SUMS')
        relabelled = magisk_zip(self.tmp / 'relabelled.zip', old, tag='v2026.11.02', sums=True, sig=sig)
        honest = magisk_zip(self.tmp / 'honest.zip', old, tag='v2026.10.01', sums=True, sig=sig)
        inst = 'installed_version() { echo v2026.10.25; }; '
        for shell in self.each_shell():
            shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
            r = self.up(shell, self.serve(old) + inst + 'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "rc=$?"',
                        MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            self.assertIn('rc=3', r.stdout, r.stderr)
            self.assertIn('checksums of another release than v2026.11.02', r.stderr)
            self.assertFalse((self.disk / '.mu300-update/mu300-kernel.tar.gz').exists())
            o = json.loads(self.up(shell, f'inspect_json "{relabelled}"', MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp).stdout)
            self.assertEqual((o['signature'], o['fits'], o['why']), ('bad', 0, 'signature'))
            for word in ('', '1'):
                r = self.up(shell, f'MU300_FROM="{relabelled}"; MU300_ALLOW_UNSIGNED={word}; MU300_ALLOW_DOWNGRADE={word}; from_begin; echo began',
                            MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
                self.assertNotIn('began', r.stdout, r.stderr)
            # under its own name it is what it is: signed, and older than what is installed
            o = json.loads(self.up(shell, f'inspect_json "{honest}"', MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp).stdout)
            self.assertEqual((o['signature'], o['downgrade'], o['fits']), ('valid', 1, 1))
            r = self.up(shell, f'MU300_FROM="{honest}"; from_begin; echo began', MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            self.assertNotIn('began', r.stdout)
            self.assertIn('older than what is installed', r.stderr)
        # signed checksums that name no release (a SHA256SUMS from before the line, signed by hand) are not "signed"
        plain = release_dir(self.tmp / 'plain')
        (plain / 'SHA256SUMS').write_text(''.join(l + '\n' for l in (plain / 'SHA256SUMS').read_text().split('\n')
                                                  if l and 'mu300-release' not in l))
        psig = self.key.sign(plain / 'SHA256SUMS')
        for shell in self.each_shell():
            shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
            r = self.up(shell, self.serve(plain) + inst + 'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "rc=$?"',
                        MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            self.assertIn('rc=3', r.stdout, r.stderr)
            self.assertIn('do not say which release', r.stderr)

    def test_checksums_swapped_between_two_downloads(self):
        # the SHA256SUMS that was verified is the one every file is checked against: the same bytes, or nothing
        rel = release_dir(self.tmp / 'rel')
        self.key.sign(rel / 'SHA256SUMS')
        evil = self.tmp / 'evil-kernel.tar.gz'
        tgz(evil, [('Image', 'another kernel', 0o644)])
        code = (self.serve(rel) + 'installed_version() { echo v2026.10.25; }; '
                'fetch_verified v2026.11.02 mu300-openwrt-luci-rootfs.tar.gz; echo "first=$?"; '
                f'printf "%s  mu300-kernel.tar.gz\n" "{sha(evil)}" > "$STAGE/SHA256SUMS"; cp "{evil}" "$STAGE/mu300-kernel.tar.gz"; '
                'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "second=$?"')
        for shell in self.each_shell():
            for pub in (self.key.pub, self.tmp / 'no.pub'):
                shutil.rmtree(self.disk / '.mu300-update', ignore_errors=True)
                r = self.up(shell, code, MU300_RELEASE_PUB=pub, TMPDIR=self.tmp)
                self.assertIn('first=0', r.stdout, r.stderr)
                self.assertIn('second=3', r.stdout, r.stderr)
                self.assertIn('changed after it was checked', r.stderr)

    def serve(self, d):
        """fetch_stdout and fetch_file read from the directory D instead of the network"""
        return (f'fetch_stdout() {{ cat "{d}/${{1##*/}}" 2>/dev/null; }}; '
                f'fetch_file() {{ cp "{d}/${{1##*/}}" "$2" 2>/dev/null; }}; sleep() {{ :; }}; ')

    def test_a_download_is_checked_against_the_signature(self):
        rel = release_dir(self.tmp / 'rel')
        sig = self.key.sign(rel / 'SHA256SUMS')
        get = 'fetch_verified v2026.11.02 mu300-kernel.tar.gz; echo "rc=$?"'
        inst = 'installed_version() { echo v2026.10.25; }; '
        stage = self.disk / '.mu300-update'
        for shell in self.each_shell():
            shutil.rmtree(stage, ignore_errors=True)
            r = self.up(shell, self.serve(rel) + inst + get, MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('signed with the project\'s release key', r.stdout)
            # a mirror that changed a file and its checksum, and left the signature (or took it away)
            evil = self.tmp / 'evil'
            shutil.rmtree(evil, ignore_errors=True)
            shutil.copytree(rel, evil)
            tgz(evil / 'mu300-kernel.tar.gz', [('Image', 'another kernel', 0o644)])
            (evil / 'SHA256SUMS').write_text(f'{sha(evil / "mu300-kernel.tar.gz")}  mu300-kernel.tar.gz\n')
            for strip in (False, True):
                if strip:
                    (evil / 'SHA256SUMS.sig').unlink()
                shutil.rmtree(stage, ignore_errors=True)
                r = self.up(shell, self.serve(evil) + inst + get, MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
                # (3: the release was refused, which no second try changes - apply says no more than that)
                self.assertIn('rc=3', r.stdout, (strip, r.stderr))
                self.assertFalse((stage / 'mu300-kernel.tar.gz').exists())
            # a release from before signing, older than what is installed: not silently either, once there is a key
            shutil.rmtree(stage, ignore_errors=True)
            unsigned = self.tmp / 'unsigned'
            shutil.rmtree(unsigned, ignore_errors=True)
            shutil.copytree(rel, unsigned)
            (unsigned / 'SHA256SUMS.sig').unlink()
            older = 'installed_version() { echo v2026.12.01; }; '
            r = self.up(shell, self.serve(unsigned) + older + get, MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            self.assertIn('rc=3', r.stdout, r.stderr)
            # apply on a refusal: nothing changed, and no hint of a network fault or a download to continue (the U30
            # Air's log of an unsigned release said both)
            r = self.up(shell, self.serve(unsigned) + older + get.replace('; echo "rc=$?"', ' || fetch_stop $?'),
                        MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
            self.assertIn('ERROR: nothing was changed\n', r.stderr)
            self.assertNotIn('mobile data', r.stderr)
            self.assertNotIn('continue the download', r.stderr)
            r = self.up(shell, 'fetch_stop 1', TMPDIR=self.tmp)
            self.assertIn('continue the download', r.stderr)
            r = self.up(shell, self.serve(unsigned) + older + get, MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp, MU300_ALLOW_UNSIGNED=1)
            self.assertIn('rc=0', r.stdout, r.stderr)
            # the same mirror, a system without a key: as before signing, the checksum is all there is
            shutil.rmtree(stage, ignore_errors=True)
            r = self.up(shell, self.serve(evil) + inst + get, MU300_RELEASE_PUB=self.tmp / 'no.pub', TMPDIR=self.tmp)
            self.assertIn('rc=0', r.stdout, r.stderr)

    @unittest.skipUnless(shutil.which('unzip') or shutil.which('busybox'), 'unzip is needed to read a Magisk zip')
    def test_a_signed_zip_needs_no_word_and_a_forged_one_gets_none(self):
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
        rel = release_dir(self.tmp / 'rel')
        sig = self.key.sign(rel / 'SHA256SUMS')
        good = magisk_zip(self.tmp / 'good.zip', rel, sums=True, sig=sig)
        nosig = magisk_zip(self.tmp / 'nosig.zip', rel, sums=True)
        forged = magisk_zip(self.tmp / 'forged.zip', rel, sums=True, sig=self.other.sign(rel / 'SHA256SUMS', self.tmp / 'f.sig'))
        for shell in self.each_shell():
            for z, state, begins, with_word in ((good, 'valid', True, True), (nosig, 'unsigned', False, True),
                                                (forged, 'bad', False, False)):
                r = self.up(shell, f'inspect_json "{z}"', MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
                o = json.loads(r.stdout)
                self.assertEqual(o['signature'], state, r.stderr)
                self.assertEqual((o['fits'], o['why']), (0, 'signature') if state == 'bad' else (1, None))
                for word, want in (('', begins), ('1', with_word)):
                    r = self.up(shell, f'MU300_FROM="{z}"; MU300_ALLOW_UNSIGNED={word}; from_begin; echo began',
                                MU300_RELEASE_PUB=self.key.pub, TMPDIR=self.tmp)
                    self.assertEqual('began' in r.stdout, want, (z.name, word, r.stderr))


class Status(test_update.Lock):
    """mu300-update status and releases: JSON a page can take as it is."""

    def fill(self):
        for os_, tag in (('ubuntu', 'v2026.10.25'), ('openwrt-luci', 'v2026.10.25'), ('openwrt-luci.old', 'v2026.10.18')):
            (self.disk / os_ / 'etc/mu300').mkdir(parents=True, exist_ok=True)
            (self.disk / os_ / 'etc/mu300/image-version').write_text(tag + '\n')
        (self.disk / 'ubuntu/etc/os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        (self.disk / 'ubuntu/usr/lib').mkdir(parents=True)
        (self.disk / 'ubuntu/usr/lib/os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        boot = self.disk / 'boot'
        boot.mkdir()
        for f, v in (('installed.tag', 'v2026.10.25'), ('installed.kernel', '6.18'), ('kernel', '6.18'),
                     ('prev.tag', 'v2026.10.18'), ('prev.kernel', '5.4'), ('prev.body', 'image'), ('prev.slot', 'b')):
            (boot / f).write_text(v + '\n')
        (self.disk / '.mu300').mkdir()
        (self.disk / '.mu300/boot-attempts').write_text('3\n')
        (self.tmp / 'default-boot').write_text('linux\n')
        (self.tmp / 'notice').write_text('mu300-linux v2026.11.02 is out (this is v2026.10.25): sudo mu300-update apply\n')

    def status(self, shell, args='', **env):
        r = self.sh(shell, f'. "{UPDATE}"; installed_version() {{ echo v2026.10.25; }}; status_json {args}', MU300_LIB=1,
                    MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock, MU300_ROOT=self.disk / 'openwrt-luci',
                    MU300_DEFAULT_BOOT=self.tmp / 'default-boot', MU300_NOTICE=self.tmp / 'notice',
                    MU300_RELEASE_PUB=self.tmp / 'none.pub', **env)
        return json.loads(r.stdout), r

    def test_status(self):
        self.fill()
        self.stub('curl', 'echo "network used" >> "$STUBLOG/net"; exit 7')
        self.stub('wget', 'echo "network used" >> "$STUBLOG/net"; exit 7')
        for shell in self.each_shell():
            o, r = self.status(shell, '--sizes')
            self.assertEqual((o['ok'], o['device'], o['installed'], o['running'], o['restart_pending']),
                             (1, 'f50', 'v2026.10.25', 'openwrt-luci', 0), r.stderr)
            by = {s['name']: s for s in o['systems']}
            self.assertEqual(set(by), {'ubuntu', 'openwrt-luci'})
            self.assertEqual((by['ubuntu']['release'], by['ubuntu']['ubuntu'], by['ubuntu']['previous']), ('v2026.10.25', '26.04', None))
            prev = by['openwrt-luci']['previous']
            self.assertEqual((prev['release'], prev['running']), ('v2026.10.18', 0))
            self.assertIsInstance(prev['kb'], int)
            k = o['kernel']
            self.assertEqual((k['choice'], k['boot'], k['boot_kernel'], k['slot']), ('6.18', 'v2026.10.25', '6.18', 'b'))
            # Ubuntu 26.04 is on this device: 5.4 is not offered (kernel_cmd refuses it)
            self.assertEqual([(c['name'], c['ok'], c['why']) for c in k['choices']],
                             [('5.4', 0, 'ubuntu-26.04'), ('6.18', 1, None), ('7.2', 1, None), ('7.3', 1, None)])
            self.assertEqual((k['previous']['release'], k['previous']['kernel'], k['previous']['usable']), ('v2026.10.18', '5.4', 1))
            self.assertEqual((o['boot_default'], o['boot_attempts'], o['boot_locked']), ('linux', 3, 0))
            self.assertEqual((o['disk']['need_one_kb'], o['disk']['need_all_kb']), (2000000, 3000000))
            self.assertIsInstance(o['disk']['free_kb'], int)
            self.assertEqual(o['lock'], {'held': 0, 'pid': None, 'command': None})
            self.assertEqual(o['notice'], 'v2026.11.02')
            self.assertEqual(o['signing']['key'], 0)
            # without --sizes nothing is added up
            o, _ = self.status(shell)
            self.assertIsNone({s['name']: s for s in o['systems']}['openwrt-luci']['previous']['kb'])
            # the note of a release with a four-field tag (FINDINGS 125)
            (self.tmp / 'notice').write_text('mu300-linux v2026.10.16.10 is out (this is v2026.10.25): sudo mu300-update apply\n')
            self.assertEqual(self.status(shell)[0]['notice'], 'v2026.10.16.10')
            (self.tmp / 'notice').write_text('mu300-linux v2026.11.02 is out (this is v2026.10.25): sudo mu300-update apply\n')
        self.assertFalse((self.tmp / 'net').exists())

    def test_status_of_an_empty_device_is_still_json(self):
        shutil.rmtree(self.disk / 'ubuntu')
        (self.tmp / 'notice').write_text('mu300-linux v1"; rm -rf / is out (this is x): y\n')
        for shell in self.each_shell():
            o, r = self.status(shell)
            self.assertEqual((o['systems'], o['kernel']['previous'], o['kernel']['boot'], o['notice'], o['boot_default']),
                             ([], None, None, None, 'android'), r.stderr)

    def test_what_is_written_into_the_files_cannot_break_the_json(self):
        self.fill()
        nasty = 'v1","x":"\\\n$(reboot)`id`\t\x01é\n'
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text(nasty)
        (self.disk / 'boot/installed.tag').write_text(nasty)
        (self.disk / 'boot/prev.tag').write_text(nasty)
        for shell in self.each_shell():
            o, r = self.status(shell)
            rel = {s['name']: s for s in o['systems']}['openwrt-luci']['release']
            self.assertRegex(rel, r'^[A-Za-z0-9._+:,()@ /-]*$')
            self.assertRegex(o['kernel']['boot'], r'^[A-Za-z0-9._+:,()@ /-]*$')

    def test_the_lock_holder_is_named_and_never_disturbed(self):
        self.fill()
        for shell in self.each_shell():
            for prelude in (':', self.NO_FLOCK):
                pid = self.hold(shell, prelude)
                o, r = self.status(shell)
                self.assertEqual((o['lock']['held'], o['lock']['pid']), (1, pid), r.stderr)
                if os.path.isdir('/proc/self'):
                    self.assertIn('mu300-update', o['lock']['command'])
                # looking did not take the lock from its holder, nor leave it taken
                rc, err, _ = self.take(shell, prelude)
                self.assertEqual(rc, 1, err)
                self.end_holders()
                o, _ = self.status(shell)
                self.assertEqual(o['lock']['held'], 0)
                rc, err, _ = self.take(shell, prelude)
                self.assertEqual(rc, 0, err)

    def github(self, pretty, releases):
        """GitHub's answer to /releases as it writes it: (tag, prerelease, draft, {asset: size})"""
        out = []
        for i, (tag, pre, draft, assets) in enumerate(releases):
            out.append({
                'url': f'https://api.github.com/repos/x/y/releases/{i}', 'html_url': f'https://github.com/x/y/releases/tag/{tag}',
                'id': i, 'author': {'login': 'someone', 'id': 1, 'html_url': 'https://github.com/someone', 'type': 'User'},
                'tag_name': tag, 'target_commitish': 'main', 'name': f'MU300 Linux {tag}', 'draft': draft, 'prerelease': pre,
                'created_at': '2026-11-01T10:00:00Z', 'published_at': '2026-11-02T10:00:00Z',
                'assets': [{'url': 'https://api.github.com/x', 'id': 9, 'name': n, 'label': '',
                            'uploader': {'login': 'github-actions[bot]', 'id': 2}, 'content_type': 'application/gzip',
                            'state': 'uploaded', 'size': s, 'download_count': 3,
                            'browser_download_url': f'https://github.com/x/y/releases/download/{tag}/{n}'} for n, s in assets.items()],
                'tarball_url': 'https://api.github.com/x/tarball', 'zipball_url': 'https://api.github.com/x/zipball',
                # notes that look like the fields this reads
                'body': 'Notes: "tag_name": "v2099.01.01", "prerelease": true,\r\n| "name": "mu300-kernel.tar.gz", "size": 1 |\n{"assets": [1]}',
            })
        return json.dumps(out, indent=2) if pretty else json.dumps(out, separators=(',', ':'))

    ASSETS = {'mu300-kernel.tar.gz': 20000000, 'mu300-kernel-6.18.tar.gz': 30000000, 'mu300-openwrt-luci-rootfs.tar.gz': 40000000,
              'mu300-ubuntu-rootfs.tar.gz': 300000000, 'mu300-ubuntu-26.04-rootfs.tar.gz': 310000000,
              'mu300-extra-lang.tar.gz': 2000000, 'mu300-update': 100000, 'SHA256SUMS': 900}

    def releases(self, shell, listing, latest):
        (self.tmp / 'list.json').write_text(listing)
        code = (f'. "{UPDATE}"; installed_version() {{ echo v2026.10.25; }}; latest_release() {{ echo "{latest}"; }}; '
                f'fetch_stdout() {{ echo "$1" >> "{self.tmp}/asked"; cat "{self.tmp}/list.json"; }}; releases_json')
        r = self.sh(shell, code, MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LOCK=self.lock)
        return json.loads(r.stdout), r

    def test_releases(self):
        self.fill()
        (self.disk / 'extra/lang/i18n').mkdir(parents=True)
        (self.disk / 'extra/lang/name').write_text('lang\n')
        (self.disk / 'extra/lang/release').write_text('v2026.10.25\n')
        signed = dict(self.ASSETS, **{'SHA256SUMS.sig': 150})
        rels = [('v2026.11.09', True, False, signed), ('test-issue-102', True, False, {}), ('v2026.11.05', True, True, self.ASSETS),
                ('v2026.11.02', False, False, self.ASSETS), ('v2026.10.30', True, False, self.ASSETS),
                ('v2026.10.25', False, False, self.ASSETS)]
        for shell in self.each_shell():
            for pretty in (True, False):
                o, r = self.releases(shell, self.github(pretty, rels), 'v2026.11.02')
                self.assertEqual((o['ok'], o['installed'], o['boot']), (1, 'v2026.10.25', 'v2026.10.25'), r.stderr)
                la = o['latest']
                self.assertEqual((la['tag'], la['title'], la['published'], la['signed'], la['systems'], la['need_kb']),
                                 ('v2026.11.02', 'MU300 Linux v2026.11.02', '2026-11-02', 0, 2, 3000000))
                self.assertEqual(la['url'], 'https://github.com/dikeckaan/mu300-linux/releases/tag/v2026.11.02')
                # what this device takes: both systems (Ubuntu's 26.04), the 6.18 kernel, the updater, the language pack
                self.assertEqual({f['name']: f['size'] for f in la['files']},
                                 {'mu300-kernel-6.18.tar.gz': 30000000, 'mu300-update': 100000,
                                  'mu300-ubuntu-26.04-rootfs.tar.gz': 310000000, 'mu300-openwrt-luci-rootfs.tar.gz': 40000000,
                                  'mu300-extra-lang.tar.gz': 2000000})
                self.assertEqual(la['download'], 382100000)
                # the newest pre-release that is a release tag, not a draft, and newer than the newest release
                self.assertEqual((o['beta']['tag'], o['beta']['signed']), ('v2026.11.09', 1))
            # no pre-release newer than the newest release: none is offered
            o, _ = self.releases(shell, self.github(True, rels[3:]), 'v2026.11.02')
            self.assertIsNone(o['beta'])
            # up to date: nothing but the kernel and the updater would be looked at
            o, _ = self.releases(shell, self.github(True, rels[5:]), 'v2026.10.25')
            self.assertEqual((o['latest']['systems'], o['latest']['need_kb']), (0, 1000000))
        self.assertIn('https://api.github.com/repos/dikeckaan/mu300-linux/releases?per_page=10', (self.tmp / 'asked').read_text())

    def test_releases_of_the_new_names(self):
        # vYYYY.MM.DD.N (FINDINGS 125): newer than every three-field tag, whatever its date; .10 after .9
        self.fill()
        rels = [('v2026.10.16.9', True, False, self.ASSETS), ('v2026.10.16.10', True, False, self.ASSETS),
                ('v2026.10.26', True, False, self.ASSETS), ('v2026.10.16.0', True, False, self.ASSETS),
                ('v2026.10.27', False, False, self.ASSETS)]
        for shell in self.each_shell():
            o, r = self.releases(shell, self.github(True, rels), 'v2026.10.27')
            self.assertEqual((o['latest']['tag'], o['beta']['tag']), ('v2026.10.27', 'v2026.10.16.10'), r.stderr)
            o, r = self.releases(shell, self.github(True, rels[:2] + [('v2026.10.17.1', False, False, self.ASSETS)]), 'v2026.10.17.1')
            self.assertEqual((o['latest']['tag'], o['beta']), ('v2026.10.17.1', None), r.stderr)

    def test_without_an_answer_it_says_offline(self):
        self.fill()
        for shell in self.each_shell():
            for listing, latest in (('', 'v2026.11.02'), (self.github(True, []), ''), ('<html>rate limit</html>', '')):
                (self.tmp / 'list.json').write_text(listing)
                code = (f'. "{UPDATE}"; latest_release() {{ echo "{latest}"; }}; '
                        f'fetch_stdout() {{ [ -s "{self.tmp}/list.json" ] && cat "{self.tmp}/list.json"; }}; releases_json; echo "rc=$?"')
                r = self.sh(shell, code, MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root)
                self.assertEqual(r.stdout, '{"ok":0,"why":"offline"}\nrc=1\n', r.stderr)
            # no newest release: the list is not asked for at all (each question waits for its retries)
            code = (f'. "{UPDATE}"; latest_release() {{ echo ""; }}; '
                    'fetch_stdout() { echo "ASKED $1" >&2; return 1; }; releases_json; echo "rc=$?"')
            r = self.sh(shell, code, MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root)
            self.assertEqual((r.stdout, r.stderr), ('{"ok":0,"why":"offline"}\nrc=1\n', ''))

    def test_another_source(self):
        # /etc/mu300/update-source: a mirror, for the command and the panel alike; root's own file, URLs only
        conf = self.tmp / 'update-source'
        show = 'source_conf; echo "[${MU300_API_URL:-}][${MU300_DOWNLOAD_URL:-}][${MU300_REPO:-}]"'
        for shell in self.each_shell():
            conf.write_text('# a mirror\nMU300_API_URL=http://192.168.0.2:8000/api\nMU300_DOWNLOAD_URL=https://mirror.example/dl\n'
                            'MU300_REPO=evil/repo\nMU300_BIN=/tmp/x\n')
            conf.chmod(0o600)
            r = self.up(shell, show, MU300_SOURCE_CONF=conf)
            self.assertEqual(r.stdout.strip(), '[http://192.168.0.2:8000/api][https://mirror.example/dl][]', r.stderr)
            # the environment wins
            r = self.up(shell, show, MU300_SOURCE_CONF=conf, MU300_API_URL='http://other/api')
            self.assertEqual(r.stdout.strip(), '[http://other/api][https://mirror.example/dl][]')
            # a value that is not a plain URL is not taken
            conf.write_text('MU300_API_URL=http://x/$(reboot)\nMU300_DOWNLOAD_URL=file:///etc\n')
            self.assertEqual(self.up(shell, show, MU300_SOURCE_CONF=conf).stdout.strip(), '[][][]')
            # nor a file others can write, nor a link
            conf.write_text('MU300_API_URL=http://x/api\n')
            conf.chmod(0o666)
            r = self.up(shell, show, MU300_SOURCE_CONF=conf)
            self.assertEqual(r.stdout.strip(), '[][][]')
            self.assertIn('writable by others', r.stderr)
            conf.chmod(0o600)
            link = self.tmp / 'link'
            link.unlink(missing_ok=True)
            link.symlink_to(conf)
            self.assertEqual(self.up(shell, show, MU300_SOURCE_CONF=link).stdout.strip(), '[][][]')
            # and the two URLs are where the release is asked for and fetched
            r = self.up(shell, 'fetch_stdout() { echo "$1" >&2; return 1; }; fetch_file() { echo "$1" >&2; return 1; }; '
                               'sleep() { :; }; source_conf; latest_release; fetch_verified v2026.11.02 mu300-update',
                        MU300_SOURCE_CONF=conf)
            self.assertIn('http://x/api/releases/latest', r.stderr)

    def test_the_status_carries_the_release_source(self):
        # what the page's field shows: the file's address, or GitHub (a panel never sees the environment, the adapter
        # clears those variables before it runs anything)
        conf = self.tmp / 'update-source'
        self.fill()
        url = 'https://proxy.example/https://github.com/dikeckaan/mu300-linux/releases/download'
        for shell in self.each_shell():
            conf.unlink(missing_ok=True)
            o, r = self.status(shell, MU300_SOURCE_CONF=conf)
            self.assertEqual(o['source'], {'files': None, 'from': 'github'}, r.stderr)
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\n')
            conf.chmod(0o600)
            o, r = self.status(shell, MU300_SOURCE_CONF=conf)
            self.assertEqual(o['source'], {'files': url, 'from': 'file'}, r.stderr)
            # a file with two lines of the same name: the reader and source_conf agree on the first one
            conf.chmod(0o600)
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\nMU300_DOWNLOAD_URL=http://second/dl\n')
            self.assertEqual(self.status(shell, MU300_SOURCE_CONF=conf)[0]['source'], {'files': url, 'from': 'file'})
            # a value that is not a plain http(s) URL, or a file others can write, is not a source
            conf.write_text('MU300_DOWNLOAD_URL=file:///etc\n')
            self.assertEqual(self.status(shell, MU300_SOURCE_CONF=conf)[0]['source'], {'files': None, 'from': 'github'})
            conf.chmod(0o666)
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\n')
            self.assertEqual(self.status(shell, MU300_SOURCE_CONF=conf)[0]['source'], {'files': None, 'from': 'github'})

    def test_the_source_command(self):
        # `mu300-update source`: show where the release files come from, put a mirror in /etc/mu300/update-source, or
        # take it out again - written through the checks source_conf reads the file with, so the next run takes it
        conf = self.tmp / 'update-source'
        self.stub('id', 'echo 0')
        url = 'https://proxy.example/https://github.com/dikeckaan/mu300-linux/releases/download'
        default = ('release files  https://github.com/dikeckaan/mu300-linux/releases/download  (default)\n'
                   'release list   https://api.github.com/repos/dikeckaan/mu300-linux  (default)\n')
        for shell in self.each_shell():
            conf.unlink(missing_ok=True)
            r = self.script(shell, UPDATE, 'source', MU300_SOURCE_CONF=conf)
            self.assertEqual(r.stdout, default, r.stderr)
            # a mirror goes in, root's own file and 0600, and the file's other lines are left as they are
            conf.write_text('# a mirror\nMU300_API_URL=http://192.168.0.2:8000/api\n')
            conf.chmod(0o600)
            r = self.script(shell, UPDATE, 'source', url, MU300_SOURCE_CONF=conf)
            self.assertEqual(r.stdout.split('\n')[0], f'release files  {url}  ({conf})', r.stderr)
            self.assertEqual(conf.read_text(),
                             f'# a mirror\nMU300_API_URL=http://192.168.0.2:8000/api\nMU300_DOWNLOAD_URL={url}\n')
            self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
            # the environment wins over the file, and is named as where the value came from
            r = self.script(shell, UPDATE, 'source', MU300_SOURCE_CONF=conf, MU300_DOWNLOAD_URL='http://other/dl')
            self.assertEqual(r.stdout.split('\n')[0], 'release files  http://other/dl  (environment)')
            # the older whole-base variable is said to be the whole base
            r = self.script(shell, UPDATE, 'source', MU300_SOURCE_CONF=conf, MU300_RELEASE_URL='http://m/flat')
            self.assertIn('(MU300_RELEASE_URL: the whole base, the release tag is not added)', r.stdout)
            # what is not a plain http(s) URL, or a second word, changes nothing
            for bad in [['file:///etc'], ['http://x/$(reboot)'], [url + 'x' * 250], ['off', 'extra']]:
                before = conf.read_text()
                r = self.script(shell, UPDATE, 'source', *bad, MU300_SOURCE_CONF=conf)
                self.assertNotEqual(r.returncode, 0, bad)
                self.assertEqual(conf.read_text(), before, bad)
            # off takes that one line out and keeps the rest; a file with nothing left in it goes itself
            self.script(shell, UPDATE, 'source', 'off', MU300_SOURCE_CONF=conf)
            self.assertEqual(conf.read_text(), '# a mirror\nMU300_API_URL=http://192.168.0.2:8000/api\n')
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\n')
            conf.chmod(0o600)
            self.script(shell, UPDATE, 'source', 'off', MU300_SOURCE_CONF=conf)
            self.assertFalse(conf.exists())

    def test_a_mirror_that_cannot_be_reached(self):
        # a mirror the device cannot reach: said once, and the rest of that run comes from github.com. An HTTP
        # error is the source's answer, and stops the run as it always did.
        conf = self.tmp / 'update-source'
        url = 'https://proxy.example/https://github.com/dikeckaan/mu300-linux/releases/download'
        field = 'a' * 64
        conf.write_text(f'MU300_DOWNLOAD_URL={url}\n')
        conf.chmod(0o600)
        # the last word of curl's arguments is the URL it was asked for; the mirror is unreachable (7), github answers
        self.stub('curl', 'for u in "$@"; do :; done\n'
                          'printf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                          f'case $u in *proxy.example*) exit 7 ;; *) printf "%s\\n" "{field}  mu300-update"; exit 0 ;; esac')
        self.stub('sleep', ':')     # fetch_verified waits between its tries; the test does not
        code = 'source_conf; fetch_verified v2026.11.02 mu300-update; echo "rc=$?"; echo "mirror: [${MU300_DOWNLOAD_URL:-}]"; echo "api: [${MU300_API_URL:-}]"'
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
            self.assertEqual(r.stderr.count('mirror unreachable: the rest comes from github.com'), 1, r.stderr)
            self.assertIn('mirror: []', r.stdout)
            # the announcement is true of both sides: an API mirror this device named is left too
            self.assertIn('api: []', r.stdout, 'a run that says github.com must not still ask an API mirror')
            urls = (self.tmp / 'urls').read_text().split()
            self.assertTrue(urls[0].startswith(url + '/v2026.11.02/'), urls[:2])
            self.assertIn('https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.02/SHA256SUMS', urls)
        # an HTTP error (22) is not a mirror that is gone: the run stops with the source still in place
        self.stub('curl', 'for u in "$@"; do :; done\nprintf "%s\\n" "$u" >> "$STUBLOG/urls"\ncase $u in *proxy.example*) exit 22 ;; *) exit 0 ;; esac')
        for shell in self.each_shell():
            r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
            self.assertEqual(r.stderr.count('mirror unreachable'), 0, r.stderr)
            self.assertIn(f'mirror: [{url}]', r.stdout)
        # and with no mirror set at all nothing is said: an unreachable GitHub is just an unreachable GitHub
        self.stub('curl', 'exit 7')
        for shell in self.each_shell():
            r = self.up(shell, code, MU300_SOURCE_CONF=self.tmp / 'no-such-file', MU300_FETCH_DELAY=0)
            self.assertEqual(r.stderr.count('mirror unreachable'), 0, r.stderr)

    def test_a_slow_mirror_is_not_an_unreachable_one(self):
        # a mirror that answers and then stalls wrote bytes before it died, so the run keeps taking the files from
        # it: the fallback is for a mirror that is not there, not for a slow line - the people who set this field
        # are often the ones who cannot reach github.com at all. A public https mirror, so the fallback would be
        # allowed if the failure counted.
        conf = self.tmp / 'update-source'
        url = 'https://mirror.example/mu300/releases/download'
        field = 'b' * 64
        conf.write_text(f'MU300_DOWNLOAD_URL={url}\n')
        conf.chmod(0o600)
        self.stub('sleep', ':')     # fetch_verified waits between its tries; the test does not
        code = 'source_conf; fetch_verified v2026.11.02 mu300-update; echo "rc=$?"; echo "mirror: [${MU300_DOWNLOAD_URL:-}]"'
        # -o FILE gets half a file and then curl's 28 (a transfer cut at --speed-time) or 56 (a reset mid-transfer);
        # the checksums come back fine
        for rc in (28, 56):
            self.stub('curl', 'for u in "$@"; do :; done\n'
                              'out=; prev=; for a in "$@"; do [ "$prev" = -o ] && out=$a; prev=$a; done\n'
                              'printf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                              f'case $u in *mirror.example*SHA256SUMS) printf "%s\\n" "{field}  mu300-update"; exit 0 ;;'
                              f' *mirror.example*) [ -z "$out" ] || printf half >> "$out"; exit {rc} ;; *) exit 0 ;; esac')
            for shell in self.each_shell():
                (self.tmp / 'urls').unlink(missing_ok=True)
                r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
                self.assertEqual(r.stderr.count('mirror unreachable'), 0, (rc, r.stderr))
                self.assertIn(f'mirror: [{url}]', r.stdout)
                urls = (self.tmp / 'urls').read_text().split()
                self.assertTrue(all(u.startswith(url) for u in urls), (rc, urls))      # github was never asked
                self.assertGreaterEqual(urls.count(f'{url}/v2026.11.02/mu300-update'), 2, urls)   # and it was retried
        # a stall before the first byte (28 with nothing written) is a mirror that is not there: github.com
        self.stub('curl', 'for u in "$@"; do :; done\n'
                          'printf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                          f'case $u in *mirror.example*SHA256SUMS) printf "%s\\n" "{field}  mu300-update"; exit 0 ;;'
                          f' *mirror.example*) exit 28 ;; *github.com*SHA256SUMS) printf "%s\\n" "{field}  mu300-update"; exit 0 ;;'
                          ' *) exit 0 ;; esac')
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
            self.assertEqual(r.stderr.count('mirror unreachable: the rest comes from github.com'), 1, r.stderr)
            urls = (self.tmp / 'urls').read_text().split()
            self.assertIn('https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.02/mu300-update', urls)

    def test_the_fetchers_tell_unreachable_from_an_answer(self):
        # fetch_unreachable_rc on its own: connect-class codes are "not there", 28/56 only when nothing arrived, an
        # HTTP error never; and nothing is a mirror's absence when no mirror is set or the run already fell back
        cases = (('7', '', '9'), ('6', '', '9'), ('35', '', '9'), ('52', '', '9'),
                 ('28', '', '1'), ('56', '', '1'), ('28', '0', '9'), ('56', '0', '9'), ('28', '1', '1'), ('7', '1', '1'),
                 ('22', '0', '1'), ('1', '0', '1'))
        code = '; '.join(f'echo "{rc}/{arr}=$(fetch_unreachable_rc {rc} {arr})"' for rc, arr, _ in cases)
        for shell in self.each_shell():
            r = self.up(shell, code, MU300_DOWNLOAD_URL='https://mirror.example/dl')
            for rc, arr, want in cases:
                self.assertIn(f'{rc}/{arr}={want}\n', r.stdout, r.stdout)
            r = self.up(shell, 'echo "[$(fetch_unreachable_rc 7 0)]"')
            self.assertEqual(r.stdout, '[1]\n')
            r = self.up(shell, 'SOURCE_FELL_BACK=1; echo "[$(fetch_unreachable_rc 7 0)]"', MU300_DOWNLOAD_URL='https://m.example/dl')
            self.assertEqual(r.stdout, '[1]\n')

    def test_the_fallback_can_be_turned_off(self):
        # MU300_MIRROR_FALLBACK=0: an update the mirror cannot serve fails, as it did before there was a mirror -
        # over a mobile link taking the release from github.com can be the more expensive answer. Only the file
        # says so, never the environment.
        conf = self.tmp / 'update-source'
        url = 'http://mirror.lan/mu300/releases/download'
        conf.write_text(f'MU300_DOWNLOAD_URL={url}\nMU300_MIRROR_FALLBACK=0\n')
        conf.chmod(0o600)
        self.stub('curl', 'for u in "$@"; do :; done\nprintf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                          'case $u in *mirror.lan*) exit 7 ;; *) exit 0 ;; esac')
        code = 'source_conf; fetch_verified v2026.11.02 mu300-update; echo "rc=$?"; echo "state: [${MU300_DOWNLOAD_URL:-} $MIRROR_FALLBACK]"'
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
            self.assertEqual(r.stderr.count('the rest comes from github.com'), 0, r.stderr)
            # said once, with the reason
            self.assertEqual(r.stderr.count('mirror unreachable: the release is not taken from github.com instead '
                                            f'(MU300_MIRROR_FALLBACK=0 in {conf})'), 1, r.stderr)
            self.assertIn(f'state: [{url} 0]', r.stdout)
            self.assertIn('cannot download SHA256SUMS of v2026.11.02', r.stderr)
            urls = (self.tmp / 'urls').read_text().split()
            self.assertTrue(all(u.startswith(url) for u in urls), urls)
        # without the line a public https mirror falls back, and the environment cannot turn it off
        self.stub('curl', 'for u in "$@"; do :; done\nprintf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                          'case $u in *mirror.example*) exit 7 ;; *) exit 0 ;; esac')
        conf.write_text('MU300_DOWNLOAD_URL=https://mirror.example/mu300/releases/download\n')
        for shell in self.each_shell():
            r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0, MU300_MIRROR_FALLBACK='0')
            self.assertIn('state: [ auto]', r.stdout)
            self.assertEqual(r.stderr.count('mirror unreachable: the rest comes from github.com'), 1, r.stderr)

    def test_a_lan_or_plain_http_mirror_does_not_fall_back_by_itself(self):
        # a copy on the LAN is usually there so the release does not come over a metered SIM: when that box is off,
        # the run says so and stops instead of quietly taking the release from github.com over the mobile link
        conf = self.tmp / 'update-source'
        self.stub('sleep', ':')
        code = 'source_conf; fetch_verified v2026.11.02 mu300-update; echo "rc=$?"'
        for url in ('http://mirror.example/dl', 'https://192.168.1.5:8443/dl', 'https://10.0.0.2/dl', 'https://172.20.1.1/dl',
                    'https://mirror.lan/dl', 'https://nas.local/dl', 'https://nas/dl', 'https://127.0.0.1:8000/dl'):
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\n')
            conf.chmod(0o600)
            self.stub('curl', 'for u in "$@"; do :; done\nprintf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                              f'case $u in {url}*) exit 7 ;; *) exit 0 ;; esac')
            for shell in self.each_shell():
                (self.tmp / 'urls').unlink(missing_ok=True)
                r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
                self.assertNotIn('rc=0', r.stdout, url)
                self.assertEqual(r.stderr.count('the rest comes from github.com'), 0, (url, r.stderr))
                self.assertEqual(r.stderr.count('a plain-http or LAN mirror; MU300_MIRROR_FALLBACK=1'), 1, (url, r.stderr))
                urls = (self.tmp / 'urls').read_text().split()
                self.assertTrue(all(u.startswith(url) for u in urls), (url, urls))      # github.com never asked
            # the line in the file allows it
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\nMU300_MIRROR_FALLBACK=1\n')
            for shell in self.each_shell():
                r = self.up(shell, code, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
                self.assertEqual(r.stderr.count('mirror unreachable: the rest comes from github.com'), 1, (url, r.stderr))
        # apply's first fetch is its self-update check, with its stderr thrown away: the line is said there all the
        # same (fd 7, the run's stderr), once, and the run's next fetch does not say it again (F50-B, 2026-10-17)
        url = 'http://192.168.79.22:8799'
        two = ('source_conf; fetch_verified v2026.11.02 mu300-update 2>/dev/null; '
               'fetch_verified v2026.11.02 mu300-ubuntu-rootfs.tar.gz; echo "rc=$?"')
        self.stub('curl', 'exit 7')
        for extra, line in (('', 'a plain-http or LAN mirror; MU300_MIRROR_FALLBACK=1'),
                            ('MU300_MIRROR_FALLBACK=1\n', 'mirror unreachable: the rest comes from github.com')):
            conf.write_text(f'MU300_DOWNLOAD_URL={url}\n' + extra)
            conf.chmod(0o600)
            for shell in self.each_shell():
                r = self.up(shell, two, MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0)
                self.assertNotIn('rc=0', r.stdout)
                self.assertEqual(r.stderr.count(line), 1, (extra, r.stderr))
        # which addresses count as local
        for shell in self.each_shell():
            for url, want in (('http://mirror.example/x', 0), ('https://mirror.example/x', 1), ('https://MIRROR.LAN/x', 0),
                              ('https://172.15.0.1/x', 1), ('https://172.32.0.1/x', 1), ('https://172.16.0.1/x', 0),
                              ('https://169.254.1.1/x', 0), ('https://router.home.arpa/x', 0), ('https://localhost:8000/x', 0),
                              ('https://192.169.0.1/x', 1), ('https://8.8.8.8/x', 1)):
                r = self.up(shell, f'mirror_local {url}; echo "rc=$?"')
                self.assertEqual(r.stdout, f'rc={want}\n', url)

    def test_the_fallback_state_goes_with_a_self_update(self):
        # apply execs the updater of the release it carries, in this very environment: it must not read the source
        # file again, wait for the same mirror and say the line a second time
        conf = self.tmp / 'update-source'
        conf.write_text('MU300_DOWNLOAD_URL=https://mirror.example/mu300/releases/download\n')
        conf.chmod(0o600)
        for shell in self.each_shell():
            r = self.up(shell, 'source_conf; mirror_fell_back; printf "child: [%s]\\n" "$(sh -c \'echo ${MU300_MIRROR_FELL_BACK:-}\')"',
                        MU300_SOURCE_CONF=conf)
            self.assertIn('child: [1]', r.stdout)
            r = self.up(shell, 'source_conf; echo "mirror: [${MU300_DOWNLOAD_URL:-}]"; echo "base: $(fetch_base v2026.11.02)"',
                        MU300_SOURCE_CONF=conf, MU300_MIRROR_FELL_BACK=1)
            self.assertIn('mirror: []', r.stdout)
            self.assertIn('base: https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.02', r.stdout)
            self.assertEqual(r.stderr.count('mirror unreachable'), 0, r.stderr)
        # and the whole download of the handed-over updater: not one request to the mirror, nothing said again
        field = 'c' * 64
        self.stub('curl', 'for u in "$@"; do :; done\nprintf "%s\\n" "$u" >> "$STUBLOG/urls"\n'
                          f'case $u in *mirror.example*) exit 7 ;; *SHA256SUMS) printf "%s\\n" "{field}  mu300-update"; exit 0 ;; *) exit 0 ;; esac')
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            r = self.up(shell, 'source_conf; fetch_verified v2026.11.02 mu300-update; echo "rc=$?"',
                        MU300_SOURCE_CONF=conf, MU300_FETCH_DELAY=0, MU300_MIRROR_FELL_BACK=1)
            urls = (self.tmp / 'urls').read_text().split()
            self.assertTrue(urls and all(u.startswith('https://github.com/') for u in urls), urls)
            self.assertEqual(r.stderr.count('mirror unreachable'), 0, r.stderr)

    def test_the_download_address(self):
        for shell in self.each_shell():
            for env, want in (({}, 'https://github.com/dikeckaan/mu300-linux/releases/download/v2026.11.02/SHA256SUMS'),
                              ({'MU300_DOWNLOAD_URL': 'http://m/dl'}, 'http://m/dl/v2026.11.02/SHA256SUMS'),
                              ({'MU300_RELEASE_URL': 'http://m/flat', 'MU300_DOWNLOAD_URL': 'http://m/dl'}, 'http://m/flat/SHA256SUMS')):
                r = self.up(shell, 'fetch_stdout() { echo "$1" >&2; return 1; }; fetch_verified v2026.11.02 mu300-update', **env)
                self.assertIn(want, r.stderr.split('\n')[0])

    def test_a_download_names_its_file_for_whoever_follows(self):
        p = self.tmp / 'progress'
        for shell in self.each_shell():
            p.unlink(missing_ok=True)
            self.up(shell, 'fetch_stdout() { return 1; }; fetch_verified v2026.11.02 mu300-kernel.tar.gz; '
                           'fetch_verified v2026.11.02 mu300-openwrt-rootfs.tar.gz', MU300_PROGRESS=p)
            self.assertEqual(p.read_text(), 'file mu300-kernel.tar.gz\nfile mu300-openwrt-rootfs.tar.gz\n')
            # and without a follower nothing is written anywhere
            r = self.up(shell, 'progress file x; echo "rc=$?"')
            self.assertEqual(r.stdout, 'rc=0\n')

    def test_the_additions_are_one_block(self):
        # what the PR says it added: every function of the block, so that a merge with other work on the updater
        # can tell at a glance what is whose
        src = UPDATE.read_text()
        block = src[src.index('# ---- for a panel: status'):src.index('# ---- for a panel: end')]
        import re
        self.assertEqual(sorted(re.findall(r'^([a-z_]+)\(\) \{', block, re.M)), sorted([
            'jv', 'jstr', 'jnum', 'mtime', 'tag_ok', 'tag_key', 'newer', 'space_needed', 'source_conf', 'key_state', 'sig_raw', 'sig_state',
            'stage_private', 'tag_sha', 'sums_trusted', 'sums_verdict', 'apply_targets', 'downgrade_ok', 'boot_downgrade_ok', 'zip_p', 'man_val', 'from_open', 'from_read', 'payload_is', 'from_close', 'from_member', 'from_fits',
            'from_installed', 'apply_options', 'from_begin', 'from_verified', 'from_handover', 'inspect_json',
            'lock_holder', 'size_kb', 'status_json', 'release_json', 'releases_json', 'progress',
            'mirror_fell_back_inherited']))


# (Status borrows Lock's holder of the lock, not its tests: those run in test_update)
for _name in dir(test_update.Lock):
    if _name.startswith('test_'):
        setattr(Status, _name, None)


# The stand-in mu300-update of the adapter's tests: status, releases and inspect print what the test put in $FAKE;
# everything else records its arguments and the release it was given, prints $FAKE/log and ends with $FAKE/rc.
FAKE_UPDATE = r'''#!/bin/sh
case ${1:-} in
    status) cat "$FAKE/status"; exit 0 ;;
    releases) [ -f "$FAKE/releases" ] && cat "$FAKE/releases"; exit "$(cat "$FAKE/releases.rc" 2>/dev/null || echo 0)" ;;
    inspect) printf '%s\n' "$*" >> "$FAKE/calls"; [ -f "$FAKE/inspect" ] && cat "$FAKE/inspect"; exit 0 ;;
esac
printf '%s|%s|%s\n' "$*" "${MU300_RELEASE:-}" "${MU300_PROGRESS:+progress}" >> "$FAKE/calls"
printf '[%s][%s][%s][%s][%s]\n' "${MU300_ALLOW_UNSIGNED:-}" "${MU300_ALLOW_DOWNGRADE:-}" "${MU300_RELEASE_PUB:-}" "${MU300_FROM:-}" \
    "${MU300_RELEASE_URL:-}${MU300_API_URL:-}${MU300_DOWNLOAD_URL:-}${MU300_FORCE:-}${MU300_FROM_SHA:-}" >> "$FAKE/env"
umask > "$FAKE/umask"
[ -z "${MU300_PROGRESS:-}" ] || [ ! -f "$FAKE/progress" ] || cat "$FAKE/progress" >> "$MU300_PROGRESS"
[ ! -f "$FAKE/log" ] || cat "$FAKE/log"
[ ! -f "$FAKE/newtag" ] || cp "$FAKE/newtag" "$MU300_DISK/openwrt-luci/etc/mu300/image-version"
exit "$(cat "$FAKE/rc" 2>/dev/null || echo 0)"
'''

STATUS = {'ok': 1, 'installed': 'v2026.10.25', 'running': 'openwrt-luci', 'lock': {'held': 0, 'pid': None, 'command': None},
          'disk': {'free_kb': 5000000, 'total_kb': 9000000, 'need_one_kb': 2000000, 'need_all_kb': 2000000}}
RELEASES = {'ok': 1, 'installed': 'v2026.10.25', 'boot': 'v2026.10.25',
            'latest': {'tag': 'v2026.11.02', 'files': [{'name': 'mu300-openwrt-luci-rootfs.tar.gz', 'size': 40000000}], 'download': 40000000},
            'beta': {'tag': 'v2026.11.09', 'files': [], 'download': 1}}


class Adapter(ShellTest):
    def setUp(self):
        super().setUp()
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        # setsid: what would run detached is written down, and the test runs it itself (run_job)
        self.stub('setsid', 'printf "%s\\n" "$*" >> "$FAKE/setsid"')
        # df: the free space the test says (KiB), for the Linux partition and for /tmp
        self.stub('df', 'case "$*" in *"$MU300_UPDATE_TMP") f=$(cat "$FAKE/free-tmp") ;; *) f=$(cat "$FAKE/free-disk") ;; esac; '
                        'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/x 99999999 1 %s 1%% /x\\n" "$f"')
        self.fake = self.tmp / 'fake'
        self.fake.mkdir()
        self.cmd = self.tmp / 'mu300-update'
        self.cmd.write_text(FAKE_UPDATE)
        self.cmd.chmod(0o755)
        self.disk = self.tmp / 'disk'
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
        self.run_dir = self.tmp / 'run'
        self.updir = self.disk / '.mu300-upload'
        self.tmpfs = self.tmp / 'tmpfs'
        self.tmpfs.mkdir()
        self.put('status', json.dumps(STATUS))
        self.put('free-disk', '5000000')
        self.put('free-tmp', '900000')

    def put(self, name, text):
        (self.fake / name).write_text(text)

    def ad(self, shell, *args, **env):
        e = dict(FAKE=self.fake, MU300_UPDATE_CMD=self.cmd, MU300_DISK=self.disk, MU300_UPDATE_RUN_DIR=self.run_dir,
                 MU300_UPDATE_TMP=self.tmpfs, MU300_UPDATE_REPORT=self.tmp / 'update-report')
        e.update(env)
        r = self.script(shell, ADAPTER, *args, **e)
        if args[0] == 'run-job':
            return r
        try:
            return json.loads(r.stdout)
        except ValueError:
            self.fail(f'not JSON: {r.stdout!r} {r.stderr!r}')

    def act(self, shell, op, tag='', systems='', kernel='', sha256='', size='', flags='', confirm='', url='', **env):
        return self.ad(shell, 'act', op, tag, systems, kernel, sha256, size, flags, confirm, url, **env)

    def started(self, wait=0.2):
        """the detached starts so far, and forgotten (a start lands a moment after the adapter has answered)"""
        f = self.fake / 'setsid'
        end = time.monotonic() + wait
        while time.monotonic() < end and not (wait > 1 and f.exists() and f.read_text().endswith('\n')):
            time.sleep(0.02)
        out = f.read_text().split('\n')[:-1] if f.exists() else []
        f.unlink(missing_ok=True)
        return out

    def calls(self):
        f = self.fake / 'calls'
        out = f.read_text().split('\n')[:-1] if f.exists() else []
        f.unlink(missing_ok=True)
        return out

    def run_job(self, shell):
        self.assertEqual(self.started(wait=5), [f'{ADAPTER} run-job'])
        r = self.ad(shell, 'run-job')
        self.assertEqual(r.returncode, 0, r.stderr)

    def reset(self):
        for d in (self.run_dir, self.updir):
            shutil.rmtree(d, ignore_errors=True)
        for f in ('calls', 'setsid', 'log', 'rc', 'newtag', 'progress', 'inspect', 'releases', 'releases.rc'):
            (self.fake / f).unlink(missing_ok=True)
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.25\n')
        self.put('status', json.dumps(STATUS))
        self.put('free-disk', '5000000')
        self.put('free-tmp', '900000')

    def check(self, shell):
        self.put('releases', json.dumps(RELEASES, separators=(',', ':')))
        self.assertEqual(self.act(shell, 'check'), {'ok': 1, 'started': 1, 'kind': 'check'})
        self.run_job(shell)

    def test_get_before_anything(self):
        for shell in self.each_shell():
            self.reset()
            o = self.ad(shell, 'get')
            self.assertEqual((o['ok'], o['status']['installed'], o['check'], o['check_age'], o['report'], o['report_new']),
                             (1, 'v2026.10.25', None, None, None, 0))
            self.assertEqual(o['upload'], {'present': 0, 'size': 0, 'inspect': None})
            self.assertEqual(o['job'], {'kind': None, 'state': 'none'})
            self.assertEqual(self.ad(shell, 'job'), {'ok': 1, 'job': {'kind': None, 'state': 'none'}})
            # whatever the updater prints, the reply is an object or an error
            for text in ('', 'garbage', '[1]', '{"ok":1} trailing "'):
                self.put('status', text)
                o = self.ad(shell, 'get')
                self.assertIn(o['ok'], (0, 1))
            self.assertEqual(self.ad(shell, 'get', MU300_UPDATE_CMD=self.tmp / 'none'),
                             {'ok': 0, 'error': 'Updating is not available on this system'})
            self.assertEqual(self.ad(shell, 'explode'), {'ok': 0, 'error': 'Unknown update operation'})

    def test_the_report_of_the_last_update(self):
        # what mu300-update's carry block writes (carry_report): the newest update on top, root's alone; the page shows
        # its start, opened while the note at login is up (update-report.new, until "mu300-update report")
        report = self.tmp / 'update-report'
        newest = ('==== openwrt-luci: v2026.10.27 -> v2026.11.02, 2026-11-02 10:00 ====\n'
                  'Everything you added to the system was carried over: 412 files, 37 MB.\n'
                  'Yours is in use where the new release has another version (the one of the release: '
                  '/etc/mu300/update-kept/v2026.10.27/image):\n  /etc/config/firewall\n'
                  'Packages installed on the device:\n  tcpdump\n\n')
        for shell in self.each_shell():
            self.reset()
            report.write_text(newest + '==== openwrt-luci: v2026.10.20 -> v2026.10.27, 2026-10-27 09:00 ====\nolder\n')
            report.chmod(0o600)
            o = self.ad(shell, 'get')
            self.assertTrue(o['report'].startswith(newest), o['report'])
            self.assertIn('v2026.10.20 -> v2026.10.27', o['report'])
            self.assertEqual(o['report_new'], 0)
            (self.tmp / 'update-report.new').write_text('')
            self.assertEqual(self.ad(shell, 'get')['report_new'], 1)
            # a long one: its start, and where the rest is
            report.write_text(newest + ''.join(f'  /home/user/file{i}\n' for i in range(2000)))
            rep = self.ad(shell, 'get')['report']
            self.assertTrue(rep.startswith(newest))
            self.assertLess(len(rep), 6200)
            self.assertTrue(rep.endswith('\n...\n(the whole report: mu300-update report)'), rep[-200:])
            # a link at its place is not followed
            report.unlink()
            report.symlink_to(self.tmp / 'elsewhere')
            (self.tmp / 'elsewhere').write_text('not the report\n')
            o = self.ad(shell, 'get')
            self.assertEqual((o['report'], o['report_new']), (None, 0))
            report.unlink()
            (self.tmp / 'update-report.new').unlink()

    def test_a_check_is_kept_for_the_page(self):
        for shell in self.each_shell():
            self.reset()
            self.check(shell)
            o = self.ad(shell, 'get')
            self.assertEqual((o['check']['latest']['tag'], o['check']['beta']['tag']), ('v2026.11.02', 'v2026.11.09'))
            self.assertEqual((o['job']['kind'], o['job']['state'], o['job']['rc']), ('check', 'done', 0))
            self.assertIsInstance(o['check_age'], int) if os.path.isdir('/proc') else None
            # no internet: the answer says so, and replaces the old one (nothing stale is offered)
            self.put('releases', '{"ok":0,"why":"offline"}')
            self.put('releases.rc', '1')
            self.act(shell, 'check')
            self.run_job(shell)
            o = self.ad(shell, 'get')
            self.assertEqual(o['check'], {'ok': 0, 'why': 'offline'})
            self.assertEqual(self.act(shell, 'apply', tag='v2026.11.02', confirm='apply')['error'], 'Check for updates first')
            # an updater that prints nothing at all (killed): offline too, never an old or half answer
            (self.fake / 'releases').unlink()
            self.act(shell, 'check')
            self.run_job(shell)
            self.assertEqual(self.ad(shell, 'get')['check'], {'ok': 0, 'why': 'offline'})

    def test_an_update_starts_only_what_the_check_offered(self):
        for shell in self.each_shell():
            self.reset()
            ok = dict(tag='v2026.11.02', confirm='apply')
            self.assertEqual(self.act(shell, 'apply', **ok)['error'], 'Check for updates first')
            self.check(shell)
            for bad, error in ((dict(ok, confirm=''), 'This needs confirmation'), (dict(ok, confirm='clean'), 'This needs confirmation'),
                               (dict(ok, tag='v2026.11.03'), 'Check for updates first'), (dict(ok, tag=''), 'Invalid update request'),
                               (dict(ok, tag='latest'), 'Invalid update request'), (dict(ok, tag='v2026.11.02;id'), 'Invalid update request'),
                               # the new names (FINDINGS 125): a release tag, only not the one the check offered
                               (dict(ok, tag='v2026.10.16.1'), 'Check for updates first'), (dict(ok, tag='v2026.10.16.10'), 'Check for updates first'),
                               (dict(ok, tag='v2026.10.16.0'), 'Invalid update request'), (dict(ok, tag='v2026.13.01.1'), 'Invalid update request'),
                               (dict(ok, systems='android'), 'Invalid update request'), (dict(ok, systems='--from'), 'Invalid update request'),
                               (dict(ok, flags='force'), 'Invalid update request'), (dict(ok, flags='--unsigned'), 'Invalid update request')):
                self.assertEqual(self.act(shell, 'apply', **bad), {'ok': 0, 'error': error}, bad)
            self.assertEqual(self.started(), [])
            self.assertEqual(self.act(shell, 'apply', systems='openwrt-luci ubuntu', flags='no_reinstall', **ok),
                             {'ok': 1, 'started': 1, 'kind': 'apply'})
            # one at a time: whatever is asked for while it runs is refused, and nothing second is started
            for op, kw in (('apply', ok), ('check', {}), ('clean', {'confirm': 'clean'}), ('kernel', {'kernel': '7.2', 'confirm': 'kernel'}),
                           ('rollback', {'confirm': 'rollback'}), ('dismiss', {}), ('upload_begin', {'size': '100'})):
                self.assertEqual(self.act(shell, op, **kw), {'ok': 0, 'error': 'An update task is already running'}, op)
            self.assertEqual(self.ad(shell, 'job')['job']['state'], 'running')
            self.put('log', '\n==> downloading v2026.11.02\nall files are here and verified; from now on the network is not needed\n'
                            '\n==> openwrt-luci: switching over\nopenwrt-luci: updated v2026.10.25 -> v2026.11.02\n')
            self.put('newtag', 'v2026.11.02\n')
            self.run_job(shell)
            # the release the page showed is the one installed, whatever is the newest by now
            self.assertEqual(self.calls(), ['apply --no-reinstall openwrt-luci ubuntu|v2026.11.02|progress'])
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['kind'], j['state'], j['rc'], j['stage'], j['changed']), ('apply', 'done', 0, 'switch', 1))
            self.assertIn('updated v2026.10.25 -> v2026.11.02', j['log'])
            # the pre-release is offered too; all systems when none is named
            self.act(shell, 'dismiss')
            self.assertEqual(self.ad(shell, 'job')['job']['state'], 'none')
            self.act(shell, 'apply', tag='v2026.11.09', confirm='apply')
            self.run_job(shell)
            self.assertEqual(self.calls(), ['apply|v2026.11.09|progress'])

    def test_how_a_task_ends(self):
        for shell in self.each_shell():
            self.reset()
            self.check(shell)
            ok = dict(tag='v2026.11.02', confirm='apply')
            # it failed and changed nothing
            self.put('rc', '1')
            self.put('log', '\n==> downloading v2026.11.02\n\nERROR: not enough free space on the Linux partition\n')
            self.act(shell, 'apply', **ok)
            self.run_job(shell)
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['state'], j['rc'], j['changed'], j['stage']), ('failed', 1, 0, 'download'))
            self.assertIn('not enough free space', j['log'])
            # it was killed (the device lost power, say): no result, so it failed, and the next one may start
            self.put('rc', '0')
            self.act(shell, 'apply', **ok)
            self.started()
            job = self.run_dir / 'update-job'
            (job / 'pid').write_text('999999\n')
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['state'], j['rc']), ('failed', 1))
            self.assertEqual(self.act(shell, 'apply', **ok)['ok'], 1)
            self.started()
            # a start whose pid is not there yet is running; one that never wrote it is not, ten seconds on
            self.assertEqual(self.ad(shell, 'job')['job']['state'], 'running')
            (job / 'start').write_text('1000\n')
            self.assertEqual(self.ad(shell, 'job')['job']['state'], 'failed')
            # a step this does not know by name is passed on as it is
            self.put('log', '\n==> openwrt-luci: carrying everything that is yours\n')
            self.act(shell, 'apply', **ok)
            self.run_job(shell)
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['stage'], j['stage_text']), ('other', 'openwrt-luci: carrying everything that is yours'))

    def test_a_download_shows_how_far_it_is(self):
        for shell in self.each_shell():
            self.reset()
            self.check(shell)
            self.act(shell, 'apply', tag='v2026.11.02', confirm='apply')
            self.started()
            job = self.run_dir / 'update-job'
            (job / 'log').write_text('\n==> downloading v2026.11.02\n')
            (job / 'progress').write_text('file mu300-kernel.tar.gz\nfile mu300-openwrt-luci-rootfs.tar.gz\n')
            (self.disk / '.mu300-update').mkdir(exist_ok=True)
            (self.disk / '.mu300-update/mu300-openwrt-luci-rootfs.tar.gz').write_bytes(b'x' * 12345)
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['state'], j['stage']), ('running', 'download'))
            self.assertEqual(j['file'], {'name': 'mu300-openwrt-luci-rootfs.tar.gz', 'have': 12345, 'total': 40000000})
            # a name that is no file name is not looked up
            (job / 'progress').write_text('file ../../etc/shadow\n')
            self.assertIsNone(self.ad(shell, 'job')['job']['file'])
            (job / 'log').write_text('\n==> downloading v2026.11.02\nall files are here and verified; from now on\n')
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['stage'], j['file']), ('verified', None))

    def test_the_updaters_lock_is_the_authority(self):
        held = dict(STATUS, lock={'held': 1, 'pid': 4242, 'command': 'sh /opt/mu300/bin/mu300-update apply'})
        for shell in self.each_shell():
            self.reset()
            self.check(shell)
            self.put('status', json.dumps(held))
            for op, kw in (('apply', dict(tag='v2026.11.02', confirm='apply')), ('rollback', {'confirm': 'rollback'}),
                           ('rollback_boot', {'confirm': 'rollback'}), ('clean', {'confirm': 'clean'}),
                           ('kernel', {'kernel': '7.2', 'confirm': 'kernel'})):
                o = self.act(shell, op, **kw)
                self.assertEqual(o, {'ok': 0, 'busy': 1, 'error': 'Another update is running on the device',
                                     'detail': 'sh /opt/mu300/bin/mu300-update apply (pid 4242)'}, op)
            self.assertEqual(self.started(), [])
            # looking for releases changes nothing on the device and needs no lock
            self.assertEqual(self.act(shell, 'check')['ok'], 1)
            self.assertEqual(len(self.started(wait=5)), 1)

    def test_going_back_cleaning_and_the_kernel(self):
        for shell in self.each_shell():
            self.reset()
            cases = [('rollback', {'confirm': 'rollback'}, 'rollback||progress'),
                     ('rollback', {'confirm': 'rollback', 'systems': 'ubuntu'}, 'rollback ubuntu||progress'),
                     ('rollback_boot', {'confirm': 'rollback'}, 'rollback-boot||progress'),
                     ('clean', {'confirm': 'clean'}, 'clean||progress'),
                     ('kernel', {'confirm': 'kernel', 'kernel': '6.18'}, 'kernel 6.18||progress')]
            for op, kw, call in cases:
                for wrong in ('', 'yes', 'apply'):
                    self.assertEqual(self.act(shell, op, **dict(kw, confirm=wrong)), {'ok': 0, 'error': 'This needs confirmation'})
                self.assertEqual(self.act(shell, op, **kw), {'ok': 1, 'started': 1, 'kind': op})
                self.run_job(shell)
                self.assertEqual(self.calls(), [call])
            for k in ('', '6.6', '6.18.3', '7.2 --from x', '-h'):
                self.assertEqual(self.act(shell, 'kernel', kernel=k, confirm='kernel'), {'ok': 0, 'error': 'Invalid update request'})
            self.assertEqual(self.act(shell, 'boot')['error'], 'Unknown update operation')
            self.assertEqual(self.started(), [])

    def test_the_release_source(self):
        # the panel's "Mirror (release files)": one mu300-update source command, run here rather than as a task (it
        # downloads nothing), and the whole update_get back so the page needs no second call
        status = json.dumps(STATUS) + '\n'
        for shell in self.each_shell():
            self.reset()
            self.put('status', status)
            for wrong in ('', 'apply', 'sourcex'):
                self.assertEqual(self.act(shell, 'source', url='https://m/dl', confirm=wrong),
                                 {'ok': 0, 'error': 'This needs confirmation'})
            for bad in ('ftp://m/dl', 'proxy.example/'):
                self.assertEqual(self.act(shell, 'source', url=bad, confirm='source'),
                                 {'ok': 0, 'error': 'Invalid update request'}, bad)
            self.assertEqual(self.calls(), [])
            # a mirror: the updater is asked for exactly that, and the reply is a whole update_get
            url = 'https://proxy.example/https://github.com/dikeckaan/mu300-linux/releases/download'
            r = self.act(shell, 'source', url=url, confirm='source')
            self.assertEqual(r['ok'], 1, r)
            self.assertEqual(r['status']['running'], 'openwrt-luci')
            self.assertEqual(r['job']['state'], 'none')
            self.assertEqual(self.calls(), [f'source {url}||'])
            self.assertEqual(self.started(), [])
            # an empty address means GitHub (mu300-update source off)
            self.reset()
            self.put('status', status)
            r = self.act(shell, 'source', confirm='source')
            self.assertEqual(r['ok'], 1, r)
            self.assertEqual(self.calls(), ['source off||'])
            # what the updater refuses is refused in the panel's own words, and nothing else happens
            self.reset()
            self.put('status', status)
            self.put('rc', '1')
            self.put('log', 'ERROR: /etc/mu300 is not ours: nothing was changed\n')
            r = self.act(shell, 'source', url=url, confirm='source')
            self.assertEqual(r, {'ok': 0, 'error': 'The release source was not changed'}, r)
            self.assertEqual(self.started(), [])

    def test_the_log_is_redacted(self):
        log = ('\n==> downloading v2026.11.02\n'
               'Downloading \'https://user:hunter2@mirror.example/dl/v2026.11.02/mu300-update?token=SECRETTOKEN\'\n'
               'Redirected to /dl/x?signature=SIGNEDVALUE on mirror.example\n'
               'uci: wireless.default_radio0.key=MyWifiPassword1\n'
               'option password \'x\' PASSWORD=TopSecret9 token: abc123def\n'
               'IMSI 286011234567890 ICCID 89900112345678901234 number +90 532 123 45 67 or 05321234567\n'
               'boot: updated to v2026.11.02 (kernel 14582784 bytes, ramdisk 9234234 bytes)\n'
               f'{"a1" * 32}  mu300-kernel.tar.gz\n'
               'progress\rprogress 50%\rprogress 100%\n\x1b[31mcolour\x1b[0m \x00\x07\n')
        secrets = ('hunter2', 'SECRETTOKEN', 'mirror.example/dl', 'SIGNEDVALUE', 'MyWifiPassword1', 'TopSecret9', 'abc123def',
                   '286011234567890', '89900112345678901234', '532 123', '05321234567', '\x1b', '\x00', '\r')
        for shell in self.each_shell():
            self.reset()
            self.put('log', log)
            self.act(shell, 'clean', confirm='clean')
            self.run_job(shell)
            out = self.ad(shell, 'job')['job']['log']
            for s in secrets:
                self.assertNotIn(s, out)
            # what says how the update went stays: the release, the sizes, the checksums
            for s in ('downloading v2026.11.02', 'kernel 14582784 bytes', 'ramdisk 9234234 bytes', 'a1' * 32, 'https://[hidden]',
                      'progress 100%'):
                self.assertIn(s, out)
            # the same for the report of the last update
            (self.tmp / 'update-report').write_text('==== openwrt-luci: v2026.10.27 -> v2026.11.02 ====\n  /etc/config/wireless\n'
                                                    'wifi key=MyWifiPassword1\nIMSI 286011234567890\n')
            rep = self.ad(shell, 'get')['report']
            self.assertIn('/etc/config/wireless', rep)
            self.assertNotIn('MyWifiPassword1', rep)
            self.assertNotIn('286011234567890', rep)

    # ---------------------------------------------------------------- the upload
    def begin(self, shell, size='1000', **kw):
        return self.act(shell, 'upload_begin', size=size, **kw)

    def test_room_is_checked_before_an_upload(self):
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.begin(shell), {'ok': 1, 'path': str(self.updir / 'update.zip')})
            self.assertEqual(self.updir.stat().st_mode & 0o777, 0o700)
            for size in ('', '0', '012', 'abc', '-1', '1e9', '10 20'):
                self.assertEqual(self.begin(shell, size)['error'], 'Invalid update request', size)
            for size in (str(2047 << 20 | 1), '9' * 10, '1' * 11, '1' * 40):
                self.assertEqual(self.begin(shell, size)['error'], 'The file is too large', size)
            # 100 MB: /tmp holds it until it is complete, and the partition holds it beside what the update needs
            self.put('free-tmp', '100000')
            self.assertEqual(self.begin(shell, '104857600')['error'], 'Not enough memory for this upload')
            self.put('free-tmp', '900000')
            self.put('free-disk', '2100000')
            self.assertEqual(self.begin(shell, '104857600')['error'], 'Not enough free space on the Linux partition')
            self.put('free-disk', '2200000')
            self.assertEqual(self.begin(shell, '104857600')['ok'], 1)
            # a df that says nothing is no room
            self.put('free-disk', '')
            self.assertEqual(self.begin(shell)['ok'], 0)

    def test_the_staging_directory_is_roots_own(self):
        elsewhere = self.tmp / 'elsewhere'
        victim = self.tmp / 'victim'
        for shell in self.each_shell():
            # a link where the directory should be: nothing is made or deleted through it
            self.reset()
            elsewhere.mkdir(exist_ok=True)
            (elsewhere / 'update.zip').write_text('theirs')
            self.updir.symlink_to(elsewhere)
            for op, kw in (('upload_begin', {'size': '1000'}), ('upload_done', {}), ('upload_cancel', {}),
                           ('apply_file', {'sha256': 'a' * 64, 'confirm': 'apply', 'flags': 'unsigned'})):
                o = self.act(shell, op, **kw)
                self.assertEqual(o['ok'], 0, op)
                self.assertIn(o['error'], ('The upload directory is not safe; nothing was changed', 'No uploaded file'))
            self.assertEqual((elsewhere / 'update.zip').read_text(), 'theirs')
            self.assertEqual(self.started(), [])
            self.updir.unlink()
            # a directory others can write into: refused as it is
            self.updir.mkdir(mode=0o777)
            self.updir.chmod(0o777)
            self.assertEqual(self.begin(shell)['error'], 'The upload directory is not safe; nothing was changed')
            shutil.rmtree(self.updir)
            # the upload itself is a link (to a file the panel has no business with): not followed, and removed
            self.assertEqual(self.begin(shell)['ok'], 1)
            victim.write_text('PK the root password file')
            (self.updir / 'update.zip').symlink_to(victim)
            self.assertEqual(self.act(shell, 'upload_done'), {'ok': 0, 'error': 'No uploaded file'})
            self.assertFalse((self.updir / 'update.zip').is_symlink())
            self.assertEqual(victim.read_text(), 'PK the root password file')
            self.assertEqual(self.started(), [])
            # nothing, an empty file, a directory: no upload
            for make in (lambda p: None, lambda p: p.write_text(''), lambda p: p.mkdir()):
                shutil.rmtree(self.updir, ignore_errors=True)
                self.begin(shell)
                make(self.updir / 'update.zip')
                self.assertEqual(self.act(shell, 'upload_done')['ok'], 0)
                self.assertEqual(self.started(), [])
            # the Linux partition is not there
            self.assertEqual(self.begin(shell, MU300_DISK=self.tmp / 'nodisk')['error'], 'The upload directory is not safe; nothing was changed')

    INSPECT = {'ok': 1, 'kind': 'zip', 'sha256': 'ab' * 32, 'size': 1000, 'tag': 'v2026.11.02', 'system': 'openwrt-luci',
               'ubuntu': None, 'kernel': '5.4', 'signature': 'valid', 'installed': 'v2026.10.25', 'downgrade': 0, 'same': 0,
               'fits': 1, 'why': None}

    def upload(self, shell, inspect):
        self.reset()
        self.assertEqual(self.begin(shell)['ok'], 1)
        (self.updir / 'update.zip').write_bytes(b'PK' + b'x' * 998)
        self.put('inspect', json.dumps(inspect))
        self.assertEqual(self.act(shell, 'upload_done'), {'ok': 1, 'started': 1, 'kind': 'inspect'})
        self.run_job(shell)
        staged = self.updir / 'staged.zip'
        # the file has a name the panel cannot write to, and that file is what was looked at
        self.assertEqual(self.calls(), [f'inspect {staged}'])
        self.assertFalse((self.updir / 'update.zip').exists())
        return staged

    def test_an_upload_is_looked_at_and_then_applied(self):
        for shell in self.each_shell():
            staged = self.upload(shell, self.INSPECT)
            o = self.ad(shell, 'get')
            self.assertEqual(o['upload'], {'present': 1, 'size': 1000, 'inspect': self.INSPECT})
            ok = dict(sha256='ab' * 32, confirm='apply')
            for bad, error in ((dict(ok, confirm=''), 'This needs confirmation'),
                               (dict(ok, sha256='cd' * 32), 'The file was not checked; upload it again'),
                               (dict(ok, sha256=''), 'Invalid update request'), (dict(ok, sha256='AB' * 32), 'Invalid update request')):
                self.assertEqual(self.act(shell, 'apply_file', **bad), {'ok': 0, 'error': error}, bad)
            self.assertEqual(self.started(), [])
            self.assertEqual(self.act(shell, 'apply_file', flags='no_reinstall', **ok), {'ok': 1, 'started': 1, 'kind': 'file'})
            self.put('newtag', 'v2026.11.02\n')
            self.run_job(shell)
            self.assertEqual(self.calls(), [f'apply --from {staged} --sha256={"ab" * 32} --no-reinstall||progress'])
            j = self.ad(shell, 'job')['job']
            self.assertEqual((j['kind'], j['state'], j['changed']), ('file', 'done', 1))
            # the upload is gone after the run, and with it what was said about it
            self.assertEqual(os.listdir(self.updir), [])
            self.assertEqual(self.ad(shell, 'get')['upload'], {'present': 0, 'size': 0, 'inspect': None})
            self.assertEqual(self.act(shell, 'apply_file', **ok)['error'], 'No uploaded file')

    def test_what_is_wrong_with_a_file_needs_a_yes_by_name(self):
        ok = dict(sha256='ab' * 32, confirm='apply')
        for shell in self.each_shell():
            for sig in ('unsigned', 'nokey', 'notool'):
                staged = self.upload(shell, dict(self.INSPECT, signature=sig))
                for flags in ('', 'downgrade', 'no_reinstall'):
                    self.assertEqual(self.act(shell, 'apply_file', flags=flags, **ok),
                                     {'ok': 0, 'error': 'This file is not signed: that needs confirmation'})
                self.assertEqual(self.act(shell, 'apply_file', flags='unsigned', **ok)['ok'], 1)
                self.run_job(shell)
                self.assertEqual(self.calls(), [f'apply --from {staged} --sha256={"ab" * 32} --unsigned||progress'])
            staged = self.upload(shell, dict(self.INSPECT, downgrade=1, signature='unsigned'))
            self.assertEqual(self.act(shell, 'apply_file', flags='unsigned', **ok)['error'],
                             'This file is older than what is installed: that needs confirmation')
            self.assertEqual(self.act(shell, 'apply_file', flags='unsigned downgrade', **ok)['ok'], 1)
            self.run_job(shell)
            self.assertEqual(self.calls(), [f'apply --from {staged} --sha256={"ab" * 32} --unsigned --downgrade||progress'])
            # never: a wrong signature, a file for another device, something that is no release
            for insp, error in ((dict(self.INSPECT, signature='bad', fits=0, why='signature'), 'This file is not for this device'),
                                (dict(self.INSPECT, signature='bad'), 'The signature of this file is wrong'),
                                (dict(self.INSPECT, fits=0, why='kernel'), 'This file is not for this device'),
                                ({'ok': 0, 'why': 'not-release'}, 'The file was not checked; upload it again')):
                self.upload(shell, insp)
                self.assertEqual(self.act(shell, 'apply_file', flags='unsigned downgrade', **ok), {'ok': 0, 'error': error})
                self.assertEqual(self.started(), [])
            # an updater that says nothing about the file: not a release
            self.upload(shell, self.INSPECT)
            (self.fake / 'inspect').unlink()
            self.act(shell, 'upload_done')
            self.run_job(shell)
            self.assertEqual(self.ad(shell, 'get')['upload']['inspect'], {'ok': 0, 'why': 'not-release'})

    def test_nothing_in_the_environment_turns_a_check_off(self):
        # --unsigned and --downgrade come from the request, by name (the flags of apply_file); what the adapter was
        # started with reaches the updater in no other way - not the word, not the key, not the source
        hostile = dict(MU300_ALLOW_UNSIGNED=1, MU300_ALLOW_DOWNGRADE=1, MU300_RELEASE_PUB='/tmp/x.pub', MU300_FROM='/tmp/x.zip',
                       MU300_FROM_SHA='cd' * 32,
                       MU300_RELEASE_URL='http://x/', MU300_API_URL='http://x/', MU300_DOWNLOAD_URL='http://x/', MU300_FORCE=1)
        for shell in self.each_shell():
            staged = self.upload(shell, self.INSPECT)
            (self.fake / 'env').unlink(missing_ok=True)
            self.assertEqual(self.act(shell, 'apply_file', sha256='ab' * 32, confirm='apply', **hostile)['ok'], 1)
            self.assertEqual(self.started(wait=5), [f'{ADAPTER} run-job'])
            r = self.ad(shell, 'run-job', **hostile)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.calls(), [f'apply --from {staged} --sha256={"ab" * 32}||progress'])
            self.assertEqual((self.fake / 'env').read_text(), '[][][][][]\n')
            # and the flags are words of a fixed set, each needed for what the file is - never taken for more
            self.upload(shell, self.INSPECT)
            for flags in ('unsigned;id', '--unsigned', 'unsigned downgrade force', 'MU300_ALLOW_UNSIGNED=1'):
                self.assertEqual(self.act(shell, 'apply_file', sha256='ab' * 32, confirm='apply', flags=flags),
                                 {'ok': 0, 'error': 'Invalid update request'})

    def test_what_the_adapter_keeps_is_roots_alone(self):
        # the task's files, the releases and what was said about an upload are read through the adapter, never as
        # files: none is readable by anyone else, whatever umask rpcd runs with. The updater itself runs under the
        # usual umask: it makes the directories of the new system.
        for shell in self.each_shell():
            staged = self.upload(shell, self.INSPECT)
            self.check(shell)
            self.put('log', 'x\n')
            self.put('progress', 'file mu300-kernel.tar.gz\n')
            old = os.umask(0)
            try:
                self.assertEqual(self.act(shell, 'apply', tag='v2026.11.02', confirm='apply')['ok'], 1)
                self.assertEqual(self.started(wait=5), [f'{ADAPTER} run-job'])
                self.assertEqual(self.ad(shell, 'run-job').returncode, 0)
            finally:
                os.umask(old)
            self.assertEqual((self.fake / 'umask').read_text().strip().lstrip('0'), '22')
            job = self.run_dir / 'update-job'
            modes = {p.name: p.stat().st_mode & 0o777 for p in list(job.iterdir()) + list(self.run_dir.glob('update-*.json'))}
            self.assertGreaterEqual(set(modes), {'log', 'progress', 'rc', 'end', 'start', 'kind', 'args', 'update-check.json',
                                                 'update-inspect.json'})
            self.assertEqual({n: m for n, m in modes.items() if m & 0o077}, {})
            for d in (job, self.run_dir, self.updir):
                self.assertEqual(d.stat().st_mode & 0o777, 0o700, d)
            self.assertEqual(staged.stat().st_mode & 0o777, 0o600)

    def test_cancel_removes_the_upload(self):
        for shell in self.each_shell():
            self.upload(shell, self.INSPECT)
            self.assertEqual(self.act(shell, 'upload_cancel'), {'ok': 1})
            self.assertEqual(os.listdir(self.updir), [])
            self.assertEqual(self.ad(shell, 'get')['upload'], {'present': 0, 'size': 0, 'inspect': None})
            # a new upload replaces an old one before it starts
            self.upload(shell, self.INSPECT)
            self.begin(shell)
            self.assertEqual(os.listdir(self.updir), [])

    def test_an_unsafe_runtime_directory_runs_nothing(self):
        for shell in self.each_shell():
            self.reset()
            (self.tmp / 'theirs').mkdir(exist_ok=True)
            self.run_dir.symlink_to(self.tmp / 'theirs')
            for args in (('get',), ('job',), ('act', 'check', '', '', '', '', '', '', '')):
                self.assertEqual(self.ad(shell, *args), {'ok': 0, 'error': 'The runtime directory is not safe; nothing was run'})
            self.assertEqual(os.listdir(self.tmp / 'theirs'), [])
            self.run_dir.unlink()


PAGE_HARNESS = r'''
const fs = require('fs');
String.prototype.format = function() {
    const args = arguments; let i = 0;
    return this.replace(/%(%|s|d)/g, (m, c) => c === '%' ? '%' : c === 'd' ? String(Math.trunc(args[i++])) : String(args[i++]));
};
const _ = (s) => s;
// a DOM small enough to read: elements with attributes, children, listeners; text is the text of the subtree
function El(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.on = {}; this.style = {}; this.own = '';
    this.classList = { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} }; }
El.prototype = {
    appendChild(c) { this.kids.push(c); return c; },
    insertBefore(c) { this.kids.unshift(c); return c; },
    addEventListener(t, f) { (this.on[t] = this.on[t] || []).push(f); },
    removeEventListener() {}, remove() {}, focus() {}, querySelector() { return new El('x'); }, querySelectorAll() { return []; },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
    get textContent() { return this.own + this.kids.map((k) => typeof k === 'string' ? k : k.textContent).join(''); },
    set textContent(v) { this.own = String(v); this.kids = []; },
    get id() { return this.attrs.id; },
    fire(t, ev) { return Promise.all((this.on[t] || []).map((f) => f(ev || {}))); },
    find(id) { if (this.attrs.id === id) return this;
        for (const k of this.kids) { if (typeof k !== 'string') { const r = k.find(id); if (r) return r; } } return null; },
};
const raw = [];     // every string that went into E() alone: LuCI would take it as HTML
const E = (tag, attrs, kids) => { const e = new El(tag);
    Object.keys(attrs || {}).forEach((k) => { if (attrs[k] != null) e.attrs[k] = attrs[k]; });
    if (e.attrs.disabled != null) e.disabled = true;
    if (typeof kids === 'string') { raw.push(kids); e.own = kids; }
    else (kids || []).forEach((k) => { if (k != null) e.kids.push(k); });
    return e; };
const dom = { content: (n, kids) => { n.own = ''; n.kids = [].concat(kids || []); },
    append: (n, kids) => { [].concat(kids || []).forEach((k) => { if (k != null) n.kids.push(k); }); } };
const body = new El('body');
const document = { getElementById: () => null, createElement: (t) => new El(t), body: body, head: new El('head'),
    documentElement: new El('html'), addEventListener: () => {}, removeEventListener: () => {} };
const window = { setInterval: () => 0, location: { reload: () => {} } };
const timers = [];
const setTimeout = (fn) => { timers.push(fn); return timers.length; };
const clearTimeout = () => {};
const getComputedStyle = () => ({ getPropertyValue: () => '' });
// the backend: canned replies by method, every call written down
const calls = [], replies = {};
const rpc = { declare: (d) => function() { const args = {}; (d.params || []).forEach((p, i) => { args[p] = arguments[i]; });
    calls.push([d.method, args]);
    const r = replies[d.method]; return Promise.resolve(typeof r === 'function' ? r(args) : r); } };
const L = { resolveDefault: (p, d) => Promise.resolve(p).catch(() => d), env: { sessionid: 'S', cgi_base: '/cgi-bin' },
    url: (p) => '/cgi-bin/luci/' + p, bind: (f, s) => f.bind(s) };
const M = new Function('rpc', 'baseclass', '_', 'document', 'getComputedStyle', 'window', 'setTimeout', 'clearTimeout',
    fs.readFileSync(process.argv[2], 'utf8'))(rpc, { extend: (o) => o }, _, document, getComputedStyle, window, setTimeout, clearTimeout);
// the page's dialogs answer yes, and say what they asked; its notices are kept
const asked = [], toasts = [];
M.confirmBox = (title, message, opts) => { asked.push([title, message, opts || {}]); return Promise.resolve(true); };
M.toast = (text, opts) => { toasts.push(text); return { update: () => {}, close: () => {} }; };
M.busy = () => {};
M.injectCss = () => {};
const posted = [];
function XMLHttpRequest() { this.upload = {}; }
XMLHttpRequest.prototype = { open(m, u) { this.m = m; this.u = u; },
    send(fd) { posted.push([this.m, this.u, fd.fields]); this.status = 200; this.responseText = JSON.stringify({ size: 3 }); this.onload(); } };
function FormData() { this.fields = []; }
FormData.prototype.append = function(k, v) { this.fields.push([k, typeof v === 'string' ? v : v.name]); };
const V = new Function('view', 'dom', 'M', '_', 'L', 'E', 'document', 'window', 'setTimeout', 'clearTimeout', 'XMLHttpRequest', 'FormData',
    fs.readFileSync(process.argv[3], 'utf8'))({ extend: (o) => o }, dom, M, _, L, E, document, window, setTimeout, clearTimeout,
    XMLHttpRequest, FormData);
const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
const open = async (state) => { replies.update_get = state; const root = V.render(await V.load()); await flush(); return root; };
const click = async (root, id) => { const e = root.find(id); if (!e) throw new Error('no #' + id); await e.fire('click'); await flush(); };
const tick = async (root, id, on) => { const e = root.find(id); if (!e) throw new Error('no #' + id); e.checked = on; await e.fire('change'); await flush(); };
const STATE = __STATE__;
(async function() { __BODY__ })().then((result) => process.stdout.write(JSON.stringify(result)),
    (e) => { console.error(e); process.exit(1); });
'''

PAGE_STATE = {
    'ok': 1,
    'status': {'ok': 1, 'device': 'f50', 'installed': 'v2026.10.25', 'running': 'openwrt-luci', 'restart_pending': 0,
               'systems': [{'name': 'openwrt-luci', 'release': 'v2026.10.25', 'ubuntu': None, 'since': 1790000000,
                            'previous': {'release': 'v2026.10.18', 'kb': 123456, 'running': 0}},
                           {'name': 'ubuntu', 'release': 'v2026.10.18', 'ubuntu': '26.04', 'since': 1780000000, 'previous': None}],
               'kernel': {'running': '6.18.3', 'choice': '6.18', 'boot': 'v2026.10.25', 'boot_kernel': '6.18', 'slot': 'b',
                          'choices': [{'name': '5.4', 'ok': 0, 'why': 'ubuntu-26.04'}, {'name': '6.18', 'ok': 1, 'why': None},
                                      {'name': '7.2', 'ok': 1, 'why': None}],
                          'previous': {'release': 'v2026.10.18', 'kernel': '6.18', 'since': 1790000000, 'usable': 1}},
               'boot_default': 'linux', 'boot_attempts': 5, 'boot_locked': 0,
               'disk': {'free_kb': 5000000, 'total_kb': 9000000, 'need_one_kb': 2000000, 'need_all_kb': 3000000},
               'lock': {'held': 0, 'pid': None, 'command': None}, 'notice': 'v2026.11.02', 'signing': {'key': 0, 'tool': 'usign'}},
    'check': {'ok': 1, 'installed': 'v2026.10.25', 'boot': 'v2026.10.25',
              'latest': {'tag': 'v2026.11.02', 'title': 'MU300 Linux v2026.11.02', 'published': '2026-11-02',
                         'url': 'https://github.com/dikeckaan/mu300-linux/releases/tag/v2026.11.02', 'signed': 0, 'systems': 2,
                         'need_kb': 3000000, 'files': [], 'download': 382100000},
              'beta': {'tag': 'v2026.11.09', 'title': 'MU300 Linux v2026.11.09', 'published': '2026-11-09',
                       'url': 'https://github.com/dikeckaan/mu300-linux/releases/tag/v2026.11.09', 'signed': 0, 'systems': 2,
                       'need_kb': 3000000, 'files': [], 'download': 382100000}},
    'check_age': 60, 'upload': {'present': 0, 'size': 0, 'inspect': None}, 'report': None,
    'job': {'kind': None, 'state': 'none'},
}


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the page test')
class Page(unittest.TestCase):
    """update.js as LuCI evaluates it, with the real common.js, a small DOM and canned replies of the backend: what
    the page shows for a state, and what each button sends."""

    def page(self, body, state=None):
        h = PAGE_HARNESS.replace('__STATE__', json.dumps(state or PAGE_STATE)).replace('__BODY__', body)
        r = subprocess.run(['node', '-', str(COMMON), str(VIEW)], input=h, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def with_(self, **change):
        s = json.loads(json.dumps(PAGE_STATE))
        for k, v in change.items():
            node = s
            *path, last = k.split('__')
            for p in path:
                node = node[p]
            node[last] = v
        return s

    def test_what_is_installed_and_offered(self):
        r = self.page('''const root = await open(STATE);
            return { text: root.textContent, calls: calls, beta: !!root.find('mud-upd-beta'), apply: !!root.find('mud-upd-apply'),
                     k54: root.find('mud-upd-kernel-pick').kids[0].attrs.disabled != null, job: root.find('mud-upd-job') };''')
        self.assertEqual(r['calls'], [['update_get', {'sizes': '1'}]])
        t = r['text']
        for want in ('openwrt-luci · v2026.10.25', 'ubuntu 26.04 · v2026.10.18', '6.18.3', 'Update to v2026.11.02', 'Release notes',
                     'If Linux fails to start 5 times in a row', 'this system has no release key', 'v2026.10.18',
                     'mu300-magisk-<release>-openwrt-luci-k6.18.zip', 'What an update keeps', 'not with Ubuntu 26.04'):
            self.assertIn(want, t)
        # the pre-release is there to be asked for, and not offered by itself
        self.assertIn('Also offer the pre-release v2026.11.09', t)
        self.assertFalse(r['beta'])
        self.assertTrue(r['apply'])
        self.assertTrue(r['k54'])
        self.assertIsNone(r['job'])

    def test_the_mirror_field(self):
        # one field under Online update: not prefilled (the address in force is the placeholder), and Save sends one
        # update_act with op=source, the address that was typed, and the operation's own word
        r = self.page('''const root = await open(STATE);
            const f = root.find('mud-upd-source'), b = root.find('mud-upd-source-save');
            const before = { calls: calls.slice(), value: f.attrs.value || '', placeholder: f.attrs.placeholder, save: !!b };
            f.value = 'https://m.example/dl';
            await click(root, 'mud-upd-source-save');
            return { before: before, calls: calls, text: root.textContent };''',
            self.with_(status__source={'files': 'https://proxy.example/https://github.com/x/y/releases/download', 'from': 'file'}))
        self.assertEqual(r['before']['value'], '')                  # nothing was filled in for the user
        self.assertEqual(r['before']['placeholder'], 'https://proxy.example/https://github.com/x/y/releases/download')
        self.assertTrue(r['before']['save'])
        self.assertEqual(r['before']['calls'], [['update_get', {'sizes': '1'}]])
        self.assertIn(['update_act', {'op': 'source', 'tag': '', 'systems': '', 'kernel': '', 'sha256': '', 'size': '',
                                      'flags': '', 'confirm': 'source', 'url': 'https://m.example/dl'}], r['calls'])
        self.assertIn('Mirror (release files)', r['text'])
        self.assertIn('Leave empty for GitHub.', r['text'])

    def test_a_saved_mirror_is_kept_and_shown(self):
        # what a Save writes is what the device reads (the Status and Adapter tests), and the page shows it once it is
        # back from the save: as the empty box's placeholder, never as something the box pretends the user typed
        r = self.page('''const root = await open(STATE);
            const f = root.find('mud-upd-source');
            const before = f.attrs.placeholder;
            f.value = 'https://m.example/dl';
            replies.update_act = { ok: 1 };
            replies.update_get = Object.assign({}, STATE,
                { status: Object.assign({}, STATE.status, { source: { files: 'https://m.example/dl', from: 'file' } }) });
            await click(root, 'mud-upd-source-save');
            await flush();
            const after = root.find('mud-upd-source');
            return { before: before, value: after.attrs.value || '', placeholder: after.attrs.placeholder,
                     sent: calls.filter((c) => c[0] === 'update_act').length };''',
            self.with_(status__source={}))
        self.assertEqual(r['before'], 'GitHub')                     # nothing set: the box says where it comes from
        self.assertEqual(r['sent'], 1)
        self.assertEqual(r['value'], '')                            # the box is not filled in for the user
        self.assertEqual(r['placeholder'], 'https://m.example/dl')  # ... and this is what the device now uses

    def test_an_update_asks_first_and_sends_what_was_shown(self):
        r = self.page('''const root = await open(STATE);
            replies.update_act = { ok: 1, started: 1, kind: 'apply' };
            replies.update_job = { ok: 1, job: { kind: 'apply', state: 'running', stage: 'download', elapsed: 5, log: '',
                file: { name: 'mu300-kernel.tar.gz', have: 10, total: 20 } } };
            await tick(root, 'mud-upd-sys-ubuntu', false);
            await tick(root, 'mud-upd-reinstall', false);
            await click(root, 'mud-upd-apply');
            const sent = calls.filter((c) => c[0] === 'update_act');
            // the page follows the task: its timer asks update_job
            const before = calls.filter((c) => c[0] === 'update_job').length;
            for (const t of timers.splice(0)) await t();
            await flush();
            return { asked: asked, sent: sent, polled: calls.filter((c) => c[0] === 'update_job').length - before,
                     stage: root.find('mud-upd-stage').textContent, file: root.find('mud-upd-file').textContent };''')
        self.assertEqual(len(r['asked']), 1)
        self.assertIn('v2026.11.02', r['asked'][0][0])
        self.assertIn('previous version is kept', r['asked'][0][1])
        self.assertEqual(r['sent'], [['update_act', {'op': 'apply', 'tag': 'v2026.11.02', 'systems': 'openwrt-luci', 'kernel': '',
                                                     'sha256': '', 'size': '', 'flags': 'no_reinstall', 'confirm': 'apply'}]])
        self.assertGreaterEqual(r['polled'], 1)
        self.assertEqual(r['stage'], 'Downloading')
        self.assertIn('mu300-kernel.tar.gz: 10.0 B / 20.0 B (50%)', r['file'])

    def test_a_running_task_is_shown_to_whoever_opens_the_page(self):
        job = {'kind': 'apply', 'state': 'running', 'started': 1, 'elapsed': 200, 'rc': None, 'stage': 'unpack', 'stage_text': 'x',
               'file': None, 'changed': 0, 'log': 'line <b>one</b>'}
        r = self.page('''const root = await open(STATE);
            const off = ['mud-upd-check', 'mud-upd-apply', 'mud-upd-upload', 'mud-upd-kernel-set', 'mud-upd-android']
                .map((id) => root.find(id).attrs.disabled != null);
            return { card: root.find('mud-upd-job').textContent, off: off, log: root.find('mud-upd-log').textContent,
                     following: timers.length, raw: raw.filter((s) => s.indexOf('<') >= 0) };''', self.with_(job=job))
        self.assertIn('Unpacking the new system', r['card'])
        self.assertIn('It goes on when this page is closed', r['card'])
        self.assertEqual(r['off'], [True] * 5)
        # the log is text: nothing of the backend ever went into E() as markup
        self.assertIn('line <b>one</b>', r['log'])
        self.assertEqual(r['raw'], [])
        self.assertGreaterEqual(r['following'], 1)

    def test_the_end_of_an_update(self):
        done = {'kind': 'apply', 'state': 'done', 'started': 1, 'elapsed': 300, 'rc': 0, 'stage': 'boot', 'stage_text': '',
                'file': None, 'changed': 1, 'log': 'updated'}
        r = self.page('''const root = await open(STATE);
            replies.act = { ok: 1 }; replies.update_act = { ok: 1 };
            const text = root.find('mud-upd-job').textContent, later = root.find('mud-upd-dismiss').textContent;
            await click(root, 'mud-upd-restart');
            const restart = calls.filter((c) => c[0] === 'act');
            await click(root, 'mud-upd-dismiss');
            return { text: text, later: later, asked: asked, restart: restart,
                     dismiss: calls.filter((c) => c[0] === 'update_act').map((c) => c[1].op) };''', self.with_(job=done))
        self.assertIn('Done. Restart the device to start it.', r['text'])
        self.assertEqual(r['later'], 'Later')
        self.assertEqual(r['asked'][0][0], 'Restart the device?')
        self.assertTrue(r['asked'][0][2].get('danger'))
        self.assertEqual(r['restart'], [['act', {'op': 'reboot', 'arg': ''}]])
        self.assertEqual(r['dismiss'], ['dismiss'])
        failed = dict(done, state='failed', rc=1, changed=0, log='ERROR: not enough free space')
        r = self.page('''const root = await open(STATE);
            return { text: root.find('mud-upd-job').textContent, restart: !!root.find('mud-upd-restart'),
                     open: root.find('mud-upd-log').open };''', self.with_(job=failed))
        self.assertIn('It did not finish. Nothing was changed', r['text'])
        self.assertIn('not enough free space', r['text'])
        self.assertFalse(r['restart'])
        self.assertTrue(r['open'])

    def test_without_internet_it_says_so(self):
        r = self.page("const root = await open(STATE); return [ root.find('mud-upd-offline').textContent, !!root.find('mud-upd-apply') ];",
                      self.with_(check={'ok': 0, 'why': 'offline'}))
        self.assertIn('GitHub could not be reached', r[0])
        self.assertIn('update from a file', r[0])
        self.assertFalse(r[1])

    def test_going_back_takes_a_tick_and_a_yes(self):
        r = self.page('''const root = await open(STATE);
            replies.update_act = { ok: 1, started: 1 };
            const out = { before: {} };
            for (const id of ['mud-upd-rollback', 'mud-upd-rollback-boot', 'mud-upd-clean']) {
                out.before[id] = root.find(id).disabled === true;
                await click(root, id);                              // not ticked: nothing happens
            }
            out.unticked = calls.filter((c) => c[0] === 'update_act').length + asked.length;
            for (const id of ['mud-upd-rollback', 'mud-upd-rollback-boot', 'mud-upd-clean']) {
                await tick(root, id + '-sure', true);
                await click(root, id);
            }
            out.asked = asked.map((a) => [a[0], !!a[2].danger]);
            out.messages = asked.map((a) => a[1]);
            out.sent = calls.filter((c) => c[0] === 'update_act').map((c) => [c[1].op, c[1].confirm]);
            return out;''')
        self.assertEqual(set(r['before'].values()), {True})
        self.assertEqual(r['unticked'], 0)
        self.assertEqual(r['asked'], [['Back to the previous version?', True], ['Back to the previous boot image?', True],
                                      ['Remove the previous version?', True]])
        self.assertIn('openwrt-luci v2026.10.18', r['messages'][0])
        self.assertEqual(r['sent'], [['rollback', 'rollback'], ['rollback_boot', 'rollback'], ['clean', 'clean']])

    def test_an_upload_goes_through_lucis_endpoint_after_room_was_made(self):
        r = self.page('''const root = await open(STATE);
            replies.update_act = (a) => a.op === 'upload_begin' ? { ok: 1, path: '/mnt/mu300-disk/.mu300-upload/update.zip' } : { ok: 1, started: 1 };
            root.find('mud-upd-pick').files = [ { name: 'mu300-magisk-v2026.11.02-openwrt-luci-k6.18.zip', size: 52428800 } ];
            await click(root, 'mud-upd-upload');
            return { sent: calls.filter((c) => c[0] === 'update_act').map((c) => [c[1].op, c[1].size]), posted: posted };''')
        self.assertEqual(r['sent'], [['upload_begin', '52428800'], ['upload_done', '']])
        self.assertEqual(r['posted'], [['POST', '/cgi-bin/cgi-upload', [['sessionid', 'S'],
                                       ['filename', '/mnt/mu300-disk/.mu300-upload/update.zip'],
                                       ['filedata', 'mu300-magisk-v2026.11.02-openwrt-luci-k6.18.zip']]]])
        # no room: nothing is posted
        r = self.page('''const root = await open(STATE);
            replies.update_act = { ok: 0, error: 'Not enough free space on the Linux partition' };
            root.find('mud-upd-pick').files = [ { name: 'x.zip', size: 5 } ];
            await click(root, 'mud-upd-upload');
            return { posted: posted, toasts: toasts };''')
        self.assertEqual(r['posted'], [])
        self.assertIn('Not enough free space on the Linux partition', r['toasts'][0])

    def test_an_uploaded_file_is_shown_before_it_is_used(self):
        insp = {'ok': 1, 'kind': 'zip', 'sha256': 'ab' * 32, 'size': 1000, 'tag': 'v2026.10.01', 'system': 'openwrt-luci',
                'ubuntu': None, 'kernel': '6.18', 'signature': 'unsigned', 'installed': 'v2026.10.25', 'downgrade': 1, 'same': 0,
                'fits': 1, 'why': None}
        body = '''const root = await open(STATE);
            replies.update_act = { ok: 1, started: 1 };
            const text = root.find('mud-upd-file').textContent;
            await click(root, 'mud-upd-apply-file');                // the confirmations are not ticked
            const early = calls.filter((c) => c[0] === 'update_act').length;
            await tick(root, 'mud-upd-unsigned', true);
            await tick(root, 'mud-upd-downgrade', true);
            await click(root, 'mud-upd-apply-file');
            return { text: text, early: early, toasts: toasts, danger: asked.map((a) => !!a[2].danger),
                     sent: calls.filter((c) => c[0] === 'update_act').map((c) => c[1]) };'''
        r = self.page(body, self.with_(upload={'present': 1, 'size': 1000, 'inspect': insp}))
        for want in ('ab' * 32, 'v2026.10.01 · openwrt-luci · kernel 6.18', 'not signed', 'nothing proves that it comes from the project',
                     'older than the installed v2026.10.25', 'the only proof of where an unsigned file comes from'):
            self.assertIn(want, r['text'])
        # a signed file is not told to be compared by hand (the note showed for one on the U30 Air)
        signed = self.page('''const root = await open(STATE); return { text: root.find('mud-upd-file').textContent,
                                  button: !!root.find('mud-upd-apply-file') };''',
                           self.with_(upload={'present': 1, 'size': 1000, 'inspect': dict(insp, signature='valid')}))
        self.assertTrue(signed['button'])
        self.assertNotIn('the only proof', signed['text'])
        self.assertEqual(r['early'], 0)
        self.assertEqual(r['toasts'], ['Tick the confirmations above first'])
        self.assertEqual(r['danger'], [True])
        self.assertEqual(r['sent'], [{'op': 'apply_file', 'tag': '', 'systems': '', 'kernel': '', 'sha256': 'ab' * 32, 'size': '',
                                      'flags': 'unsigned downgrade', 'confirm': 'apply'}])
        # a file for another kernel, or with a wrong signature, has no button at all
        for why, sig, says in (('kernel', 'unsigned', 'Take the zip for kernel 6.18'), ('signature', 'bad', 'it is never installed')):
            bad = dict(insp, fits=0, why=why, signature=sig)
            r = self.page("const root = await open(STATE); return [ root.find('mud-upd-file').textContent, !!root.find('mud-upd-apply-file'), "
                          "!!root.find('mud-upd-upload-cancel') ];", self.with_(upload={'present': 1, 'size': 1000, 'inspect': bad}))
            self.assertIn(says, r[0])
            self.assertEqual(r[1:], [False, True])
        r = self.page("const root = await open(STATE); return [ root.find('mud-upd-refused').textContent, !!root.find('mud-upd-apply-file') ];",
                      self.with_(upload={'present': 1, 'size': 5, 'inspect': {'ok': 0, 'why': 'not-release'}}))
        self.assertIn('not a release file of this project', r[0])
        self.assertFalse(r[1])

    def test_the_kernel_and_android(self):
        r = self.page('''const root = await open(STATE);
            replies.update_act = { ok: 1, started: 1 }; replies.act = { ok: 1 };
            await click(root, 'mud-upd-kernel-set');                // the kernel in use: nothing to do
            const same = calls.filter((c) => c[0] === 'update_act').length;
            root.find('mud-upd-kernel-pick').value = '7.2';
            await click(root, 'mud-upd-kernel-set');
            await click(root, 'mud-upd-android');
            return { same: same, asked: asked.map((a) => [a[0], !!a[2].danger]),
                     sent: calls.filter((c) => c[0] === 'update_act' || c[0] === 'act').map((c) => c[1]) };''')
        self.assertEqual(r['same'], 0)
        self.assertEqual(r['asked'], [['Change the kernel to 7.2?', True], ['Start Android at the next restart?', True]])
        self.assertEqual(r['sent'], [{'op': 'kernel', 'tag': '', 'systems': '', 'kernel': '7.2', 'sha256': '', 'size': '', 'flags': '',
                                      'confirm': 'kernel'}, {'op': 'os', 'arg': 'android'}])

    def test_someone_elses_update_stops_the_page(self):
        r = self.page('''const root = await open(STATE);
            return [ root.find('mud-upd-foreign').textContent ].concat(['mud-upd-check', 'mud-upd-apply', 'mud-upd-kernel-set']
                .map((id) => root.find(id).attrs.disabled != null));''',
                      self.with_(status__lock={'held': 1, 'pid': 4242, 'command': 'sh /opt/mu300/bin/mu300-update apply'}))
        self.assertIn('sh /opt/mu300/bin/mu300-update apply (pid 4242)', r[0])
        self.assertEqual(r[1:], [True, True, True])

    def test_a_backend_that_cannot_answer(self):
        r = self.page("const root = await open(STATE); return root.find('mud-upd-error').textContent;",
                      {'ok': 0, 'error': 'Updating is not available on this system'})
        self.assertEqual(r, 'Updating is not available on this system')

    def test_an_unsigned_release_is_not_offered_where_signatures_are_checked(self):
        # mu300-update refuses it there and the page has no word to give: it says so instead of an Update button that
        # can only fail (on the U30 Air it was offered, and the task failed with a hint at the network)
        js = '''const root = await open(STATE);
            return { apply: !!root.find('mud-upd-apply'), note: (root.find('mud-upd-unsigned-online') || {}).textContent || null,
                     reinstall: !!root.find('mud-upd-reinstall') };'''
        st = self.with_(status__signing={'key': 1, 'tool': 'usign'}, check__latest__signed=0)
        r = self.page(js, st)
        self.assertFalse(r['apply'])
        self.assertIn('not signed, and this system checks signatures', r['note'])
        self.assertFalse(r['reinstall'])      # (the choices of an Update button, without one: seen on the U30 Air)
        # signed, or a system without the key (as before signing): offered
        for change in ({'status__signing': {'key': 1, 'tool': 'usign'}, 'check__latest__signed': 1},
                       {'status__signing': {'key': 0, 'tool': 'usign'}, 'check__latest__signed': 0}):
            r = self.page(js, self.with_(**change))
            self.assertEqual((r['apply'], r['note'], r['reinstall']), (True, None, True), change)

    def test_no_form_buttons(self):
        # nothing on the page is a form: LuCI's Save & Apply, Save and Reset did nothing there (seen on the U30 Air)
        src = VIEW.read_text(encoding='utf-8')
        self.assertIn('handleSaveApply: null, handleSave: null, handleReset: null', src)

    def test_nothing_from_the_backend_is_markup(self):
        # LuCI's E(tag, attrs, 'string') sets innerHTML: every value that is not a literal or a _() message goes in
        # as [ value ] (the same rule, and the same check, as the Languages page)
        import re
        src = VIEW.read_text(encoding='utf-8')
        self.assertEqual(re.findall(r"E\('\w+', \{[^}]*\}, (?!_\(|\[|')[A-Za-z][^\n]*", src), [])
        self.assertNotIn('innerHTML', src)


if __name__ == '__main__':
    unittest.main()
