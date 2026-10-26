"""A release's Magisk zip unpacked on this computer (tools/package.sh, START-HERE.md in the zip): the installers then
install its system and its kernel from its own files, and download nothing. The window offers exactly that - no
other system, kernel or release - and asks GitHub nothing.

The zip's computer/ directory (the TOP of this window) holds the file .mu300-package; MU300_PACKAGE names another
unpacked zip. The manifest is read as the zip's own installer and tools/package.sh read it: only its keys, every value
exactly one a release has. The checksums are the installer's to check (it stops with a mismatch before it writes).
"""
import os
import re

from . import TOP

SYSTEMS = {'openwrt': ('openwrt', '', 'mu300-openwrt-rootfs.tar.gz'),
           'openwrt-luci': ('openwrt-luci', '', 'mu300-openwrt-luci-rootfs.tar.gz'),
           'ubuntu-24.04': ('ubuntu', '24.04', 'mu300-ubuntu-rootfs.tar.gz'),
           'ubuntu-26.04': ('ubuntu', '26.04', 'mu300-ubuntu-26.04-rootfs.tar.gz')}
KERNELS = {'5.4': 'mu300-kernel.tar.gz', '6.18': 'mu300-kernel-6.18.tar.gz', '7.2': 'mu300-kernel-7.2.tar.gz', '7.3': 'mu300-kernel-7.3.tar.gz'}
KEYS = ('TAG', 'SYSTEM', 'OS', 'UBUNTU', 'KERNEL', 'KERNEL_ASSET', 'ROOTFS_ASSET', 'SHA256_KERNEL', 'SHA256_ROOTFS')


def find(top=TOP, environ=None):
    """The directory of the unpacked zip, or None (a copy of the project)."""
    environ = os.environ if environ is None else environ
    if environ.get('MU300_PACKAGE'):
        return environ['MU300_PACKAGE']
    if os.path.isfile(os.path.join(top, '.mu300-package')):
        return os.path.dirname(os.path.abspath(top))
    return None


def parse(text):
    """The manifest's values, or None when it is not the manifest of a release's zip."""
    m = dict.fromkeys(KEYS, '')
    for line in text.splitlines():
        k, sep, v = line.partition('=')
        if sep and k in m:
            m[k] = v.rstrip('\r')
    if not re.fullmatch(r'v[0-9][0-9A-Za-z._-]*', m['TAG']) or '..' in m['TAG']:
        return None
    if SYSTEMS.get(m['SYSTEM']) != (m['OS'], m['UBUNTU'], m['ROOTFS_ASSET']):
        return None
    if KERNELS.get(m['KERNEL']) != m['KERNEL_ASSET'] or (m['UBUNTU'] == '26.04' and m['KERNEL'] == '5.4'):
        return None
    if not all(re.fullmatch(r'[0-9a-f]{64}', m[k]) for k in ('SHA256_KERNEL', 'SHA256_ROOTFS')):
        return None
    return m


def load(top=TOP, environ=None):
    """{'dir', 'tag', 'system', 'os', 'ubuntu', 'kernel', 'files': {name: bytes}, 'bytes'} of the unpacked zip this
    window installs from, {'dir', 'error'} when there is one but its manifest is not valid, or None."""
    d = find(top, environ)
    if not d:
        return None
    d = os.path.abspath(d)
    try:
        with open(os.path.join(d, 'mu300', 'manifest'), encoding='utf-8', errors='replace') as f:
            m = parse(f.read())
    except OSError:
        m = None
    if m is None:
        return {'dir': d, 'error': 'manifest'}
    files = {}
    for name in (m['KERNEL_ASSET'], m['ROOTFS_ASSET']):
        try:
            files[name] = os.path.getsize(os.path.join(d, 'payload', name))
        except OSError:
            files[name] = 0
    return {'dir': d, 'tag': m['TAG'], 'system': m['SYSTEM'], 'os': m['OS'], 'ubuntu': m['UBUNTU'], 'kernel': m['KERNEL'],
            'files': files, 'bytes': sum(files.values())}
