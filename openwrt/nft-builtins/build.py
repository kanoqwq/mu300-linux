#!/usr/bin/env python3
"""Build metadata-only APKs for the MU300 kernels' built-in nft expressions."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


FEATURES = {
    'socket': ('CONFIG_NFT_SOCKET', 'CONFIG_NF_SOCKET_IPV4', 'CONFIG_NF_SOCKET_IPV6'),
    'tproxy': ('CONFIG_NFT_TPROXY', 'CONFIG_NF_TPROXY_IPV4', 'CONFIG_NF_TPROXY_IPV6'),
}


def build(apk, configs, output, version, resolved=()):
    # Both the 5.4 config and the common mainline fragment must enable these expressions.
    # The family helpers are selected by their Kconfig; the live pre-install checks them too.
    for path in configs:
        lines = set(path.read_text().splitlines())
        required = ('CONFIG_NFT_SOCKET', 'CONFIG_NFT_TPROXY', 'CONFIG_INET', 'CONFIG_IPV6')
        missing = [key for key in required if key + '=y' not in lines]
        if missing:
            raise ValueError(str(path) + ': not built in: ' + ', '.join(missing))
    # A resolved config must also include every hidden IPv4/IPv6 helper.
    for path in resolved:
        lines = set(path.read_text().splitlines())
        missing = [key for keys in FEATURES.values() for key in keys if key + "=y" not in lines]
        if missing:
            raise ValueError(str(path) + ": not built in: " + ", ".join(missing))
    output.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name('pre-install.sh.in').read_text()
    with tempfile.TemporaryDirectory(prefix='mu300-nft-apk-') as directory:
        # mkpkg stores owner names. Resolve this build user's UID/GID to root in a
        # private lookup root; neither root privileges nor host account files are needed.
        identities = Path(directory) / 'identities'
        (identities / 'etc').mkdir(parents=True)
        passwd = 'root:x:0:0:root:/:/bin/sh\n'
        group = 'root:x:0:\n'
        if os.getuid():
            passwd += 'root:x:%d:%d:root:/:/bin/sh\n' % (os.getuid(), os.getgid())
        if os.getgid():
            group += 'root:x:%d:\n' % os.getgid()
        (identities / 'etc/passwd').write_text(passwd)
        (identities / 'etc/group').write_text(group)
        tool = [str(apk)] if apk else [
            'docker', 'run', '--rm', '--network=none',
            '-e', 'SOURCE_DATE_EPOCH=0',
            '-v', directory + ':' + directory + ':ro',
            '-v', str(output.resolve()) + ':' + str(output.resolve()) + ':rw',
            'alpine:3.23', '/sbin/apk']
        for feature, symbols in FEATURES.items():
            name = 'kmod-nft-' + feature
            stage = Path(directory) / feature
            marker = Path('usr/share/mu300/nft-builtins') / name
            (stage / marker).parent.mkdir(parents=True)
            (stage / marker).write_text(
                'MU300 built-in kernel capability; this package contains no .ko.\n' +
                '\n'.join(key + '=y' for key in symbols) + '\n')
            # OpenWrt's package-pack.mk includes this owned file list in each APK.
            listing = stage / 'lib/apk/packages' / (name + '.list')
            listing.parent.mkdir(parents=True)
            listing.write_text('/' + marker.as_posix() + '\n')
            for path in [stage, *stage.rglob('*')]:
                path.chmod(0o755 if path.is_dir() else 0o644)
            guard = Path(directory) / (feature + '-pre-install')
            guard.write_text(template.replace('@FEATURE@', feature).replace('@SYMBOLS@', ' '.join(symbols)))
            guard.chmod(0o755)
            target = output.resolve() / (name + '-' + version + '.apk')
            subprocess.run([*tool, '--root', str(identities), 'mkpkg', '--no-xattrs', '--info', 'name:' + name,
                '--info', 'version:' + version, '--info', 'arch:aarch64_generic',
                '--info', 'license:GPL-2.0-only', '--info', 'origin:mu300-nft-builtins',
                '--info', 'description:MU300 built-in nft ' + feature + ' capability (no kernel module)',
                '--script', 'pre-install:' + str(guard), '--files', str(stage), '--output', str(target)],
                env={**os.environ, 'SOURCE_DATE_EPOCH': '0'}, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', default='', help='host apk-tools with mkpkg; default: the Alpine 3.23 Docker image')
    parser.add_argument('--config', type=Path, action='append', required=True)
    parser.add_argument('--resolved-config', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default='2026.11.07-r1')
    args = parser.parse_args()
    if not shutil.which(args.apk or 'docker'):
        parser.error('Docker or host apk-tools with mkpkg is required; set MU300_APK_MKPKG for build-rootfs.sh')
    build(args.apk, args.config, args.output, args.version, args.resolved_config)


if __name__ == '__main__':
    main()
