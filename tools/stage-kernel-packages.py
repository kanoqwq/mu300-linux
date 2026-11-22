#!/usr/bin/env python3
"""Build-time APK ownership for the native kernel, never a module ABI bypass.

Generate a real APK and let apk produce its installed record in a private root.
Only the clean image's distribution kernel/kmod records are replaced. Firmware
updates subsequently merge this record with the user's unrelated packages.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

TOP = Path(__file__).resolve().parents[1]
ORIGIN = 'unisoc-native-kernel'

# These are capabilities of the configured native kernel, not aliases for an
# arbitrary feed's kernel ABI. A modular feature must also have its actual .ko.
# Empty lists describe APIs that Kbuild folds into another built-in/module.
FEATURES = {
    'bluetooth': ('BT BT_BREDR BT_LE BT_RFCOMM BT_BNEP', 'bluetooth rfcomm bnep'),
    'crypto-aead': ('CRYPTO_AEAD', ''), 'crypto-cmac': ('CRYPTO_CMAC', 'cmac'),
    'crypto-crc32c': ('CRYPTO_CRC32C', ''), 'crypto-ecb': ('CRYPTO_ECB', ''),
    'crypto-ecdh': ('CRYPTO_ECDH', 'ecdh_generic'),
    'crypto-hash': ('CRYPTO_HASH', ''), 'crypto-hmac': ('CRYPTO_HMAC', 'hmac'),
    'crypto-kpp': ('CRYPTO_KPP', ''), 'crypto-manager': ('CRYPTO_MANAGER', ''),
    'crypto-null': ('CRYPTO_NULL', ''), 'crypto-rng': ('CRYPTO_RNG CRYPTO_DRBG', ''),
    'crypto-sha3': ('CRYPTO_SHA3', 'sha3_generic'),
    'crypto-sha512': ('CRYPTO_SHA512', 'sha512|sha512_generic'),
    'crypto-blake2b': ('CRYPTO_BLAKE2B', ''), 'crypto-xxhash': ('CRYPTO_XXHASH', ''),
    'fs-btrfs': ('BTRFS_FS', 'btrfs'), 'fs-ext4': ('EXT4_FS', 'ext4'),
    'fs-vfat': ('VFAT_FS', 'vfat'), 'fs-exfat': ('EXFAT_FS', 'exfat'),
    'fs-fuse': ('FUSE_FS', 'fuse'), 'fs-xfs': ('XFS_FS', 'xfs'),
    'fs-nfs': ('NFS_FS', 'nfs'), 'fs-cifs': ('CIFS', 'cifs'),
    'hid': ('HID', 'hid'), 'input-core': ('INPUT', ''), 'input-evdev': ('INPUT_EVDEV', 'evdev'),
    'lib-crc-ccitt': ('CRC_CCITT', 'crc_ccitt'), 'lib-crc16': ('CRC16', 'crc16'),
    'lib-crc32c': ('LIBCRC32C', ''),
    'lib-raid6': ('RAID6_PQ', 'raid6_pq'), 'lib-xor': ('XOR_BLOCKS', 'xor'),
    'lib-lzo': ('LZO_COMPRESS LZO_DECOMPRESS', ''),
    'lib-zlib-inflate': ('ZLIB_INFLATE', ''), 'lib-zlib-deflate': ('ZLIB_DEFLATE', ''),
    'lib-zstd': ('ZSTD_COMPRESS ZSTD_DECOMPRESS', ''),
    'nf-conntrack': ('NF_CONNTRACK', 'nf_conntrack'),
    'nf-conntrack6': ('NF_CONNTRACK IPV6', ''),
    'nf-conntrack-netlink': ('NF_CT_NETLINK', 'nf_conntrack_netlink'),
    'nf-flow': ('NF_FLOW_TABLE', 'nf_flow_table'),
    'nf-ipt': ('IP_NF_IPTABLES', 'ip_tables'), 'nf-ipt6': ('IP6_NF_IPTABLES', 'ip6_tables'),
    'nf-log': ('NF_LOG_SYSLOG', 'nf_log_syslog'), 'nf-log6': ('NF_LOG_SYSLOG IPV6', ''),
    'nf-nat': ('NF_NAT', 'nf_nat'), 'nf-reject': ('NF_REJECT_IPV4', 'nf_reject_ipv4'),
    'nf-reject6': ('NF_REJECT_IPV6', 'nf_reject_ipv6'),
    'nf-socket': ('NF_SOCKET_IPV4 NF_SOCKET_IPV6', 'nf_socket_ipv4 nf_socket_ipv6'),
    'nf-tproxy': ('NF_TPROXY_IPV4 NF_TPROXY_IPV6', 'nf_tproxy_ipv4 nf_tproxy_ipv6'),
    'nf-ipvs': ('IP_VS IP_VS_IPV6 IP_VS_PROTO_TCP IP_VS_PROTO_UDP IP_VS_NFCT '
                'IP_VS_RR IP_VS_WRR IP_VS_LC IP_VS_SH NETFILTER_XT_MATCH_IPVS',
                'ip_vs ip_vs_rr ip_vs_wrr ip_vs_lc ip_vs_sh xt_ipvs'),
    'nfnetlink': ('NETFILTER_NETLINK', ''),
    'nfnetlink-queue': ('NETFILTER_NETLINK_QUEUE', 'nfnetlink_queue'),
    'nft-core': ('NF_TABLES NFT_CT', 'nf_tables'),
    'nft-fib': ('NFT_FIB_INET', 'nft_fib_inet'),
    'nft-nat': ('NFT_NAT NFT_MASQ', 'nft_nat'),
    'nft-offload': ('NFT_FLOW_OFFLOAD NF_FLOW_TABLE_INET', 'nft_flow_offload'),
    'nft-compat': ('NFT_COMPAT', 'nft_compat'), 'nft-queue': ('NFT_QUEUE', 'nft_queue'),
    'nft-socket': ('NFT_SOCKET', 'nft_socket'), 'nft-tproxy': ('NFT_TPROXY', 'nft_tproxy'),
    'ipt-core': ('IP_NF_IPTABLES IP_NF_FILTER NETFILTER_XTABLES', 'iptable_filter'),
    'ipt-extra': ('NETFILTER_XT_MATCH_ADDRTYPE NETFILTER_XT_MATCH_OWNER '
                  'NETFILTER_XT_MATCH_PKTTYPE NETFILTER_XT_MATCH_QUOTA',
                  'xt_addrtype xt_owner xt_pkttype xt_quota'),
    'ipt-conntrack': ('NF_CONNTRACK NETFILTER_XT_MATCH_CONNTRACK '
                      'NETFILTER_XT_MATCH_STATE NETFILTER_XT_TARGET_CT',
                      'nf_conntrack xt_conntrack xt_state xt_CT'),
    'ipt-nat': ('IP_NF_NAT NETFILTER_XT_NAT NETFILTER_XT_TARGET_MASQUERADE '
                'NETFILTER_XT_TARGET_REDIRECT', 'iptable_nat xt_nat xt_MASQUERADE xt_REDIRECT'),
    'ipt-nat6': ('IP6_NF_NAT', 'ip6table_nat'),
    'ipt-physdev': ('BRIDGE_NETFILTER NETFILTER_XT_MATCH_PHYSDEV', 'br_netfilter xt_physdev'),
    'ip6tables': ('IP6_NF_IPTABLES IP6_NF_FILTER', 'ip6table_filter'),
    'nls-base': ('NLS', ''), 'nls-cp437': ('NLS_CODEPAGE_437', 'nls_cp437'),
    'nls-iso8859-1': ('NLS_ISO8859_1', 'nls_iso8859_1'),
    'nls-utf8': ('NLS_UTF8', 'nls_utf8'),
    'ppp': ('PPP PPP_ASYNC', 'ppp_generic ppp_async'),
    'pppoe': ('PPPOE', 'pppoe'), 'pppox': ('PPPOE', 'pppox'),
    'regmap-core': ('REGMAP', ''), 'slhc': ('SLHC', 'slhc'),
    'sound-core': ('SND SND_PCM', 'snd snd_pcm soundcore'),
    'tun': ('TUN', 'tun'), 'veth': ('VETH', 'veth'),
    'dummy': ('DUMMY', 'dummy'),
    'inet-diag': ('INET_DIAG INET_TCP_DIAG INET_UDP_DIAG', 'inet_diag tcp_diag udp_diag'),
    'br-netfilter': ('BRIDGE_NETFILTER', 'br_netfilter'),
    'usb-net-rtl8152': ('USB_RTL8152', 'r8152'),
    'usb-storage': ('USB_STORAGE', 'usb_storage'),
}


def records(raw):
    result = {}
    for row in raw.split('\n\n'):
        name = next((s[2:] for s in row.splitlines() if s.startswith('P:')), None)
        if name:
            if name in result:
                raise ValueError('Duplicate installed package: ' + name)
            result[name] = row.strip('\n')
    return result


def field(row, key):
    return next((s[2:] for s in row.splitlines() if s.startswith(key + ':')), '')


def normalize(name):
    return Path(name).name.removesuffix('.ko').replace('-', '_')


def capabilities(config, modules, builtin):
    available = set(modules) | builtin
    config = dict(config)
    # New kernels folded libcrc32c into lib/crc/crc32 (crc32c.h includes
    # crc32.h). A disabled old CONFIG_LIBCRC32C is not the same as its removal.
    if 'CONFIG_LIBCRC32C' not in config and 'crc32' in available:
        config['CONFIG_LIBCRC32C'] = config.get('CONFIG_CRC32')
    result = []
    for name, (flags, names) in FEATURES.items():
        values = [config.get('CONFIG_' + f) for f in flags.split()]
        if not all(v in ('y', 'm') for v in values):
            continue
        if not all(any(n in available for n in group.split('|')) for group in names.split()):
            continue
        # APIs with no independent module are only declared when built in.
        if not names and 'm' in values:
            continue
        result.append('kmod-' + name)
    return sorted(result)


def apk_file(root, files, output, info):
    """APK v2 is supported by OpenWrt apk-tools 3; no host SDK is needed."""
    data = io.BytesIO()
    with gzip.GzipFile(fileobj=data, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w|', format=tarfile.PAX_FORMAT) as tar:
            dirs = sorted({str(p) for name in files for p in Path(name).parents if str(p) != '.'},
                          key=lambda n: (n.count('/'), n))
            for name in dirs:
                member = tarfile.TarInfo(name); member.type = tarfile.DIRTYPE; member.mode = 0o755
                tar.addfile(member)
            for name in sorted(files):
                path = root / name
                member = tar.gettarinfo(str(path), arcname=name)
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = 'root'
                if member.isfile():
                    member.pax_headers = {'APK-TOOLS.checksum.SHA1': hashlib.sha1(path.read_bytes()).hexdigest()}
                    with path.open('rb') as stream: tar.addfile(member, stream)
                elif member.issym(): tar.addfile(member)
                else: raise ValueError('Unexpected package payload: ' + name)
    payload = data.getvalue()
    info += 'datahash = ' + hashlib.sha256(payload).hexdigest() + '\n'
    control = io.BytesIO()
    with tarfile.open(fileobj=control, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        member = tarfile.TarInfo('.PKGINFO'); member.mode = 0o644; member.size = len(info.encode())
        tar.addfile(member, io.BytesIO(info.encode()))
    # APK joins the control and data tar streams at a gzip boundary. An end
    # marker in the control stream would hide the payload from the tar reader.
    control_size = 512 + ((len(info.encode()) + 511) // 512) * 512
    output.write_bytes(gzip.compress(control.getvalue()[:control_size], mtime=0) + payload)


def stage(root, config_path, image, release, platform, apk_image='u30pro-rootfs:25.12.5', output=None):
    root = Path(root).resolve()
    if root == Path('/') or not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise ValueError('Use a clean offline root and a valid kernel release')
    if platform not in ('e5', 'mu300', 'u30pro'):
        raise ValueError('Explicit kernel platform required')
    db = root / 'lib/apk/db/installed'
    old = records(db.read_text())
    if 'ubox' not in old or not (root / 'sbin/kmodloader').is_file():
        raise ValueError('ubox kmodloader is required; do not invent a loader package')
    modroot = root / 'lib/modules' / release
    config = dict(s.split('=', 1) for s in Path(config_path).read_text().splitlines()
                  if s.startswith('CONFIG_') and '=' in s)
    for line in Path(config_path).read_text().splitlines():
        match = re.fullmatch(r'# (CONFIG_[A-Z0-9_]+) is not set', line)
        if match: config[match[1]] = 'n'
    modules = {normalize(p.name): p for p in modroot.glob('*.ko')}
    if not modules or len(modules) != len(list(modroot.glob('*.ko'))):
        raise ValueError('Missing/colliding native modules')
    for name, path in modules.items():
        match = re.search(rb'(?:^|\x00)vermagic=([^ \x00]+)', path.read_bytes())
        if not match or match[1].decode() != release:
            raise ValueError('Module/kernel release mismatch: ' + name)
    image_hash = hashlib.sha256(Path(image).read_bytes()).hexdigest()
    version = re.search(rb'Linux version ([^\s\x00]+)', Path(image).read_bytes())
    if not version or version[1].decode() != release:
        raise ValueError('Image/kernel release mismatch')
    builtin = {normalize(s) for s in (modroot / 'modules.builtin').read_text().splitlines() if s}
    provided = capabilities(config, modules, builtin)
    # The revision identifies this packaged payload, not a claim that every
    # module with the same uname is compatible. Never provide the distro ABI.
    inputs = {'format': 1, 'release': release, 'provides': provided,
              'config': hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
              'image': image_hash, 'modules': {n: hashlib.sha256(p.read_bytes()).hexdigest()
                                             for n, p in sorted(modules.items())}}
    revision = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    base = re.match(r'\d+\.\d+\.\d+', release)
    if not base: raise ValueError('Invalid native kernel version')
    native_version = base[0] + '~' + revision[:32] + '-r1'
    retired = {n for n in old if n == 'kernel' or n.startswith('kmod-')}
    # Check direct userland requirements. A stale distro record must not hide
    # a genuinely missing feature in the selected native kernel configuration.
    needed = {re.split(r'[<>=~]', d, 1)[0] for n, row in old.items() if n not in retired
              for d in field(row, 'D').split() if d.startswith('kmod-')}
    missing = needed - set(provided)
    if missing:
        raise ValueError('Native kernel lacks installed userland requirements: ' + ', '.join(sorted(missing)))
    receipt = root / 'usr/share/unisoc-kernel/package.json'
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(format=1, platform=platform, kernel_release=release,
        package_version=native_version, payload_sha256=revision, image_sha256=image_hash,
        provides=provided), sort_keys=True, indent=2) + '\n')
    files = {p.relative_to(root).as_posix() for p in modroot.rglob('*') if p.is_file() or p.is_symlink()}
    files.add(receipt.relative_to(root).as_posix())
    # Take over the clean image's existing module autoload metadata, not ubox
    # itself or unrelated packages' files. This also makes ownership queryable.
    for n in retired:
        directory = ''
        for line in old[n].splitlines():
            if line.startswith('F:'): directory = line[2:]
            elif line.startswith('R:') and directory in ('etc/modules.d', 'etc/modules-boot.d'):
                path = directory + '/' + line[2:]
                target = root / path
                if target.is_file():
                    entries = [s for s in target.read_text().splitlines()
                               if s.strip() and not s.lstrip().startswith('#')
                               and normalize(s.split()[0]) in set(modules) | builtin]
                    if entries:
                        target.write_text('\n'.join(entries)+'\n')
                        files.add(path)
                    else:
                        target.unlink()
    arch = field(old['kernel'], 'A')
    info = (f'pkgname = kernel\npkgver = {native_version}\npkgdesc = Native {platform} kernel {release}\n'
            f'arch = {arch}\nlicense = GPL-2.0-only\norigin = {ORIGIN}\nbuilddate = 0\n'
            f'url = https://github.com/kanoqwq/{"e5-linux" if platform == "e5" else "u30pro_openwrt" if platform == "u30pro" else "mu300-linux"}\n'
            f'size = {sum((root / n).lstat().st_size for n in files)}\ndepend = ubox\n'
            'provides = kernel-any\n')
    with tempfile.TemporaryDirectory(prefix='native-kernel-', dir=root.parent) as td:
        temp = Path(td); apk = temp / 'kernel.apk'
        apk_file(root, files, apk, info)
        packages = [apk]
        for name in provided:
            path = temp/(name+'.apk')
            # Keep real installed kmod names visible in LuCI. Their matching
            # files (or built-ins) belong to the inseparable firmware kernel.
            apk_file(root, set(), path, f'pkgname = {name}\npkgver = {native_version}\n'
                     f'pkgdesc = Native {release} capability {name}\narch = {arch}\n'
                     f'license = GPL-2.0-only\norigin = {ORIGIN}\nbuilddate = 0\nsize = 0\n'
                     f'depend = kernel={native_version}\nprovides = {name}-any\n')
            packages.append(path)
        private_db = temp / 'db/lib/apk/db'
        private_db.mkdir(parents=True)
        private_db.joinpath('installed').write_text('\n\n'.join(
            row for n, row in old.items() if n not in retired) + '\n\n')
        private_world = temp / 'db/etc/apk/world'
        private_world.parent.mkdir(parents=True)
        private_world.write_text(''.join(n+'='+field(row, 'V')+'\n'
                                       for n, row in old.items() if n not in retired))
        args = ['--root', str(temp / 'db'), '--initdb', '--arch', arch, '--no-network',
                '--allow-untrusted', 'add', '--scripts=no', '--commit-hooks=no', *map(str, packages)]
        # Only a private package database is used; no service or package hooks.
        if shutil.which('apk'):
            command = ['apk', *args]
        else:
            command = ['docker', 'run', '--rm', '--ulimit', 'core=0', '--network', 'none', '--platform', 'linux/arm64',
                       '-v', f'{temp}:{temp}', '--entrypoint', '/usr/bin/apk', apk_image, *args]
        subprocess.run(command, check=True, timeout=120)
        generated = records((temp / 'db/lib/apk/db/installed').read_text())
        native = {n: generated[n] for n in ['kernel', *provided]}
        if output:
            shutil.copyfile(apk, output)
    merged = {n: row for n, row in old.items() if n not in retired}
    merged.update(native)
    world = root / 'etc/apk/world'
    lines = [s for s in world.read_text().splitlines() if re.split(r'[<>=~@]', s, 1)[0] not in retired]
    # Keep the bundled capabilities selected. Otherwise a later unrelated apk
    # transaction could garbage-collect their metadata as orphan dependencies.
    lines.extend(n+'='+native_version for n in ['kernel', *provided])
    # The record and world are prepared completely before either is published.
    db.with_suffix('.new').write_text('\n\n'.join(merged[n] for n in sorted(merged)) + '\n\n')
    world.with_suffix('.new').write_text('\n'.join(lines) + '\n')
    db.with_suffix('.new').replace(db); world.with_suffix('.new').replace(world)
    scripts = root / 'lib/apk/db/scripts.tar'
    if scripts.is_file():
        with tarfile.open(scripts) as tar:
            saved = [(e, tar.extractfile(e).read()) for e in tar if e.isfile()
                     and not any(e.name.startswith(n + '-') for n in retired)]
        with tarfile.open(scripts.with_suffix('.new'), 'w') as tar:
            for e, value in saved: tar.addfile(e, io.BytesIO(value))
        scripts.with_suffix('.new').replace(scripts)
    triggers = root / 'lib/apk/db/triggers'
    if triggers.is_file():
        sums = {field(old[n], 'C') for n in retired}
        triggers.write_text(''.join(s + '\n' for s in triggers.read_text().splitlines()
                                    if s.split() and s.split()[0] not in sums))
    for n in retired:
        for p in (root / 'lib/apk/packages').glob(n + '.*'):
            if p.is_file(): p.unlink()
    print(f'Native kernel APK: {release}, {len(modules)} modules, {len(provided)} capabilities')
    return json.loads(receipt.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root', 'config', 'image'): parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--platform', required=True, choices=('e5', 'mu300', 'u30pro'))
    parser.add_argument('--apk-image', default='u30pro-rootfs:25.12.5')
    parser.add_argument('--output', type=Path)
    a = parser.parse_args()
    stage(a.root, a.config, a.image, a.release, a.platform, a.apk_image, a.output)
