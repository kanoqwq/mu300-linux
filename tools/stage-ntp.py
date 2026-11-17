#!/usr/bin/env python3
"""Integrate the ordered startup into stock sysntpd without replacing its policy."""
import argparse
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]


def stage(root, overlay=None):
    root = Path(root)
    overlay = Path(overlay) if overlay else TOP / 'openwrt/overlay'
    path = root / 'etc/init.d/sysntpd'
    text = path.read_text()
    edits = {
        'PROG=/usr/sbin/ntpd': 'PROG=/opt/mu300/bin/mu300-ntpd',
        '\tfor peer in $server; do\n\t\tprocd_append_param command -p $peer\n\tdone':
            '\tprocd_set_param env "MU300_NTP_SERVERS=$server"',
        '\t\tprocd_add_jail ntpd ubus':
            '\t\tprocd_add_jail ntpd ubus\n'
            '\t\tprocd_add_jail_mount /usr/sbin/ntpd /opt/mu300/bin/busybox',
    }
    for old, new in edits.items():
        if text.count(new) == 1:
            continue
        if text.count(old) != 1 or new in text:
            raise ValueError(f'sysntpd integration anchor changed: {old!r}')
        text = text.replace(old, new)
    wrapper = 'opt/mu300/bin/mu300-ntpd'
    source = (overlay / wrapper).read_bytes().replace(b'\r\n', b'\n')
    if not (root / 'opt/mu300/bin/busybox').is_file():
        raise ValueError('Missing NTP startup dependency: opt/mu300/bin/busybox')
    (root / wrapper).write_bytes(source)
    (root / wrapper).chmod(0o755)
    path.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--overlay', type=Path)
    args = parser.parse_args()
    stage(args.root, args.overlay)
