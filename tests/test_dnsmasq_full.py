"""Real APK variant replacement in an isolated OpenWrt container, no device writes."""
from pathlib import Path
import subprocess

TOP = next(p for p in Path(__file__).resolve().parents if (p/'installer').is_dir())


def main():
    # Both historical full-root builders and incremental profile assembly must
    # select the variant, so a retained base cannot silently restore the lite one.
    sources = [TOP/'openwrt/build-rootfs.sh']
    for name in ('tools/u30pro-package.py', 'openwrt/build-profile-tf.py'):
        if (TOP/name).is_file(): sources.append(TOP/name)
    for source in sources:
        assert 'dnsmasq-full' in source.read_text(), source
    script = r'''set -eu
apk_retry() { n=1; while ! apk "$@"; do [ "$n" -lt 3 ] || return 1; n=$((n+1)); sleep 3; done; }
mkdir -p /etc/dnsmasq.d
printf '\n# user static lease and custom resolver\n' >> /etc/config/dhcp
printf '\n# user dnsmasq directives\n' >> /etc/dnsmasq.conf
printf 'address=/test.example/192.168.77.1\n' > /etc/dnsmasq.d/test.conf
sha256sum /etc/config/dhcp /etc/dnsmasq.conf /etc/dnsmasq.d/test.conf > /tmp/before
apk_retry update >/dev/null
apk_retry add --no-scripts dnsmasq-full >/dev/null
sha256sum -c /tmp/before
apk info -e dnsmasq-full >/dev/null
! grep -qx 'P:dnsmasq' /lib/apk/db/installed
test -r /lib/apk/packages/dnsmasq-full.conffiles
test ! -e /lib/apk/packages/dnsmasq.conffiles
dnsmasq --version > /tmp/features
cat /tmp/features
for feature in DHCPv6 DNSSEC nftset conntrack; do
    grep -qw "$feature" /tmp/features
    ! grep -qw "no-$feature" /tmp/features
done
dnsmasq --test --conf-file=/etc/dnsmasq.conf
'''
    subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                    '--entrypoint', '/bin/sh', 'mu300-openwrt-base:25.12.5',
                    '-ec', script], check=True)
    print('PASS full APK replaces lite, preserves all custom DHCP/DNS files and exposes full features')


if __name__ == '__main__': main()
