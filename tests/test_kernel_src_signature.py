"""upstream/verify-kernel-src.sh (FINDINGS 161): a kernel.org tarball is unpacked only when the autosigner's signed
sha256sums.asc lists its SHA-256 and a pinned releaser signed the uncompressed tar.

The pinned keyrings are checked offline (the fingerprints of the committed keys, parsed from the OpenPGP packets,
against the .fpr lists, the README and the list below). The fail-closed logic runs against throwaway keys made with
gpg in a scratch GNUPGHOME (skipped where gpg, gpgv or xz are missing); the real kernel.org files were checked by
hand (FINDINGS 161), as the build workflows do on every run."""
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from helpers import TOP

UP = TOP / 'upstream'
KEYS = UP / 'keys'
SCRIPT = UP / 'verify-kernel-src.sh'
AUTOSIGNER = {'B8868C80BA62A1FFFAF5FDA9632D3A06589DA6B1'}
RELEASERS = {'ABAF11C65A2970B130ABE3C479BE3E4300411886',   # Linus Torvalds
             '647F28654894E3BD457199BE38DBBDC86092693E',   # Greg Kroah-Hartman
             'E27E5D8A3403A2EF66873BBCDEA66FF797772CDC'}   # Sasha Levin


def packets(data):
    """(tag, body) of each OpenPGP packet (RFC 4880 4.2, old and new formats)."""
    i = 0
    while i < len(data):
        b = data[i]
        if not b & 0x80:
            raise ValueError('not an OpenPGP packet at %d' % i)
        if b & 0x40:                       # new format
            tag = b & 0x3f
            l1 = data[i + 1]
            if l1 < 192:
                n, h = l1, 2
            elif l1 < 224:
                n, h = ((l1 - 192) << 8) + data[i + 2] + 192, 3
            elif l1 == 255:
                n, h = int.from_bytes(data[i + 2:i + 6], 'big'), 6
            else:
                raise ValueError('partial body length in a key packet')
        else:                              # old format
            tag, lt = (b >> 2) & 0x0f, b & 3
            h = 1 + (1, 2, 4)[lt]
            n = int.from_bytes(data[i + 1:i + h], 'big')
        yield tag, data[i + h:i + h + n]
        i += h + n


def primary_fingerprints(path):
    """The v4 fingerprints of the primary keys (tag 6) in a binary keyring: SHA-1 of 0x99, length, body."""
    out = []
    for tag, body in packets(path.read_bytes()):
        if tag == 6:
            assert body[0] == 4, 'v4 keys only'
            out.append(hashlib.sha1(b'\x99' + len(body).to_bytes(2, 'big') + body).hexdigest().upper())
    return out


class PinnedKeys(unittest.TestCase):
    def test_keyrings_hold_exactly_the_pinned_keys(self):
        for name, want in (('kernel-autosigner', AUTOSIGNER), ('kernel-releasers', RELEASERS)):
            got = primary_fingerprints(KEYS / (name + '.gpg'))
            self.assertEqual(sorted(got), sorted(want), name)
            fpr = set((KEYS / (name + '.fpr')).read_text().split())
            self.assertEqual(fpr, want, name + '.fpr')

    def test_readme_documents_every_fingerprint(self):
        readme = (KEYS / 'README.md').read_text()
        for f in AUTOSIGNER | RELEASERS:
            self.assertIn('`%s`' % f, readme)

    def test_keyrings_are_binary_in_git(self):
        self.assertIn('*.gpg binary', (TOP / '.gitattributes').read_text().splitlines())


class BuildUsesIt(unittest.TestCase):
    def test_build_verifies_before_unpacking(self):
        body = (UP / 'build.sh').read_text()
        fetch = body[body.index('if [ ! -d /src/linux-$KV ]'):body.index('cd /src/linux-$KV')]
        for f in ('linux-$KV.tar.xz', 'linux-$KV.tar.sign', 'sha256sums.asc'):
            self.assertIn(f, fetch)
        verify = fetch.index('bash /work/verify-kernel-src.sh "$dl" "$KV" || { rm -rf "$dl"; exit 1; }')
        self.assertLess(verify, fetch.index('tar -xJf'))
        self.assertNotIn('sha256sum -c', fetch, 'the unsigned check is gone')
        self.assertEqual(body.count('tar -xJf'), 1)

    def test_checksum_list_from_kernel_org_itself(self):
        # FINDINGS 206: the CDN's sha256sums.asc lagged 7.3 by hours; the tarball and its signature come from the CDN,
        # the signed list from www.kernel.org
        body = (UP / 'build.sh').read_text()
        fetch = body[body.index('if [ ! -d /src/linux-$KV ]'):body.index('cd /src/linux-$KV')]
        self.assertIn('base=https://cdn.kernel.org/pub/linux/kernel/v${KV%%.*}.x', fetch)
        self.assertIn('[ "$f" != sha256sums.asc ] || u=https://www.kernel.org/pub/linux/kernel/v${KV%%.*}.x/$f', fetch)

    def test_build_image_has_gpgv(self):
        self.assertRegex((UP / 'Dockerfile').read_text(), r'\bgpgv\b')


TOOLS = all(shutil.which(t) for t in ('gpg', 'gpgv', 'xz', 'bash'))


@unittest.skipUnless(TOOLS, 'needs gpg, gpgv and xz')
class FailClosed(unittest.TestCase):
    KV = '9.9.9'

    @classmethod
    def setUpClass(cls):
        # a short path: gpg-agent's socket lives in GNUPGHOME
        cls.tmp = tempfile.mkdtemp(prefix='ks', dir='/tmp' if os.path.isdir('/tmp') else None)
        cls.home = os.path.join(cls.tmp, 'g')
        os.mkdir(cls.home, 0o700)
        cls.fpr = {n: cls.genkey(n) for n in ('auto', 'rel', 'other')}
        cls.keys = os.path.join(cls.tmp, 'keys')
        os.mkdir(cls.keys)
        for name, who in (('kernel-autosigner', 'auto'), ('kernel-releasers', 'rel')):
            cls.export(who, os.path.join(cls.keys, name + '.gpg'))
            with open(os.path.join(cls.keys, name + '.fpr'), 'w') as f:
                f.write(cls.fpr[who] + '\n')
        # the release: a small tar, its xz, the releaser's detached signature over the tar, the clearsigned list
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as t:
            data = b'obj-y += mu300.o\n'
            info = tarfile.TarInfo('linux-%s/Makefile' % cls.KV)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        cls.tar = buf.getvalue()
        cls.txz = subprocess.run(['xz', '-c'], input=cls.tar, stdout=subprocess.PIPE, check=True).stdout
        cls.sign = cls.gpg('rel', ['--detach-sign', '--armor'], cls.tar)
        cls.sums = cls.sums_for(cls.txz)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(['gpgconf', '--kill', 'all'], env=dict(os.environ, GNUPGHOME=cls.home),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def run_gpg(cls, args, data=None):
        return subprocess.run(['gpg', '--batch', '--yes', '--quiet', '--pinentry-mode', 'loopback',
                               '--passphrase', ''] + args, input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=dict(os.environ, GNUPGHOME=cls.home), check=True).stdout

    @classmethod
    def genkey(cls, name):
        uid = '%s <%s@example.invalid>' % (name, name)
        cls.run_gpg(['--quick-gen-key', uid, 'ed25519', 'sign', 'never'])
        out = cls.run_gpg(['--with-colons', '--list-keys', '=' + uid]).decode()
        return [line.split(':')[9] for line in out.splitlines() if line.startswith('fpr:')][0]

    @classmethod
    def export(cls, who, path):
        with open(path, 'wb') as f:
            f.write(cls.run_gpg(['--export', '--export-options', 'export-minimal', cls.fpr[who]]))

    @classmethod
    def gpg(cls, who, args, data):
        return cls.run_gpg(['--local-user', cls.fpr[who]] + args, data)

    @classmethod
    def sums_for(cls, txz, who='auto', name=None):
        name = name or 'linux-%s.tar.xz' % cls.KV
        listing = ('%s  linux-9.9.8.tar.xz\n%s  %s\n%s  linux-9.9.9.tar.gz\n'
                   % ('1' * 64, hashlib.sha256(txz).hexdigest(), name, '2' * 64)).encode()
        return cls.gpg(who, ['--clearsign'], listing)

    def release(self, txz=None, sign=None, sums=None, keys=None, path=None):
        d = tempfile.mkdtemp(dir=self.tmp)
        for name, data in (('linux-%s.tar.xz' % self.KV, self.txz if txz is None else txz),
                           ('linux-%s.tar.sign' % self.KV, self.sign if sign is None else sign),
                           ('sha256sums.asc', self.sums if sums is None else sums)):
            if data is not False:
                with open(os.path.join(d, name), 'wb') as f:
                    f.write(data)
        env = dict(os.environ, MU300_KERNEL_KEYS=keys or self.keys)
        if path is not None:
            env['PATH'] = path
        return subprocess.run([shutil.which('bash'), str(SCRIPT), d, self.KV], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)

    def assertRefused(self, r, why):
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(why, r.stderr)

    def test_good_release_passes(self):
        r = self.release()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('tar signed by a pinned releaser', r.stdout)

    def test_tampered_tarball(self):
        self.assertRefused(self.release(txz=self.txz + b'\0'), 'the signed sha256sums.asc says')

    def test_tampered_signed_list(self):
        bad = self.sums.replace(hashlib.sha256(self.txz).hexdigest().encode(), b'0' * 64)
        self.assertNotEqual(bad, self.sums)
        self.assertRefused(self.release(sums=bad), 'no good signature from the pinned kernel.org autosigner')

    def test_unsigned_line_outside_the_signed_block_is_ignored(self):
        evil = subprocess.run(['xz', '-c'], input=b'evil', stdout=subprocess.PIPE, check=True).stdout
        line = ('%s  linux-%s.tar.xz\n' % (hashlib.sha256(evil).hexdigest(), self.KV)).encode()
        r = self.release(txz=evil, sums=line + self.sums)
        self.assertRefused(r, 'the signed sha256sums.asc says')

    def test_list_without_a_line_for_the_tarball(self):
        sums = self.sums_for(self.txz, name='linux-9.9.10.tar.xz')
        self.assertRefused(self.release(sums=sums), 'no line for it')

    def test_list_signed_by_another_key(self):
        self.assertRefused(self.release(sums=self.sums_for(self.txz, who='other')),
                           'no good signature from the pinned kernel.org autosigner')

    def test_list_signed_by_a_releaser_is_not_the_autosigner(self):
        self.assertRefused(self.release(sums=self.sums_for(self.txz, who='rel')),
                           'no good signature from the pinned kernel.org autosigner')

    def test_tar_sign_over_other_data(self):
        self.assertRefused(self.release(sign=self.gpg('rel', ['--detach-sign', '--armor'], self.tar + b'x')),
                           'no good signature from a pinned kernel releaser')

    def test_tar_sign_by_an_unknown_key(self):
        self.assertRefused(self.release(sign=self.gpg('other', ['--detach-sign', '--armor'], self.tar)),
                           'no good signature from a pinned kernel releaser')

    def test_key_in_the_keyring_but_not_pinned(self):
        keys = tempfile.mkdtemp(dir=self.tmp)
        for f in os.listdir(self.keys):
            shutil.copy(os.path.join(self.keys, f), keys)
        with open(os.path.join(keys, 'kernel-releasers.gpg'), 'ab') as f:
            f.write(self.run_gpg(['--export', '--export-options', 'export-minimal', self.fpr['other']]))
        r = self.release(sign=self.gpg('other', ['--detach-sign', '--armor'], self.tar), keys=keys)
        self.assertRefused(r, 'which is not pinned')

    def test_missing_signature_file(self):
        self.assertRefused(self.release(sign=False), 'missing or empty')

    def test_empty_keyring(self):
        keys = tempfile.mkdtemp(dir=self.tmp)
        for f in os.listdir(self.keys):
            shutil.copy(os.path.join(self.keys, f), keys)
        open(os.path.join(keys, 'kernel-autosigner.gpg'), 'w').close()
        self.assertRefused(self.release(keys=keys), 'missing or empty')

    def test_no_gpgv(self):
        b = tempfile.mkdtemp(dir=self.tmp)
        for t in ('bash', 'xz', 'awk', 'grep', 'sha256sum', 'shasum', 'cut', 'mktemp', 'rm', 'cat', 'dirname', 'wc'):
            p = shutil.which(t)
            if p:
                os.symlink(p, os.path.join(b, t))
        self.assertRefused(self.release(path=b), 'gpgv not found')


if __name__ == '__main__':
    unittest.main()
