#!/usr/bin/env python3
"""Generate ueventd-perms.sh: apply Android device node ownership (ueventd.rc + init *.rc chown/chmod)
on plain Linux, because modem_control and refnotify open /dev/modem etc. as Android's system user, not as root.
usage: gen-ueventd-perms.py vendor/etc/ueventd.rc vendor-init/*.rc > ueventd-perms.sh
(android-vendor/ueventd/ holds the lines of this device's files the generator reads:
 gen-ueventd-perms.py android-vendor/ueventd/ueventd.rc android-vendor/ueventd/init-perms.rc)

Android's ids are not taken over as numbers. Android's system user is uid 1000, and on a distribution uid 1000 is
the first human account (ubuntu; alarm on Arch): the modem's calibration partitions (nr_fixnv*), persist and the
modem's control nodes were that account's, writable without sudo (FINDINGS: "The vendor daemons' own account").
What each Android id becomes on Linux (MAP below):
  system, radio -> the vendor account mu300vnd ($V, group $VG): the ids the vendor daemons run as. Where there is no
                   such account (OpenWrt, the initramfs) that is uid/gid 1000, which they switch to themselves.
  graphics      -> on a node Android gives everyone (mali0, ion, dma_heap: 0666) the video group ($GPU), so the GPU
                   is its users' and no longer everyone's; anywhere else root, as no Linux account is in it
  root and every other Android id (audio, camera, bluetooth, nfc, media, ...: services that do not run here)
                -> root
Modes: nothing is left writable by everyone, and a /dev node gets no bits at all for others (a 0666 Android node is
0660 here); a node that is root's alone keeps no group bits either (0600).
Nothing is more open than with the numbers taken over (tests/test_vendor_ids.py compares every node with the script
of v2026.10.18) but for the vendor account itself, which is new, and the RTC, below.
"""
import sys

AID = {'root': 0, 'system': 1000, 'radio': 1001, 'bluetooth': 1002, 'graphics': 1003, 'input': 1004, 'audio': 1005,
       'camera': 1006, 'log': 1007, 'wifi': 1010, 'media': 1013, 'dhcp': 1014, 'gps': 1021, 'nfc': 1027,
       'audioserver': 1041, 'cameraserver': 1047, 'shell': 2000, 'net_bt_admin': 3001, 'net_bt': 3002,
       'inet': 3003, 'net_raw': 3004}
# Android id -> (Linux owner, Linux group) as the script writes them; ids not here are root's
MAP = {'system': ('$V', '$VG'), 'radio': ('$V', '$VG')}
# Nodes that stay root's, in the vendor group: the RTC. One that belonged to Android's system could not be opened by
# systemd-timedated (root, but without CAP_DAC_OVERRIDE), and with systemd 259 (Ubuntu 26.04) timedatectl failed as a
# whole ("Failed to read RTC"); refnotify opens it too (as uid 1000 with gid 0 it did so through Linux's root:root
# 0660, as mu300vnd it needs the group).
ROOT_OWNED = ('/dev/rtc',)
# Nodes Android gives its system user that no vendor daemon run here uses, and that were the first human's on Linux:
# root's alone (0600). Input events (every key pressed), the USB serial console ttyGS* (getty and login hand it to
# whoever logs in, as on any Linux), and the firmware search path (whoever writes it chooses the firmware the kernel
# loads).
ROOT_ONLY = ('/dev/input/', '/dev/ttyGS', '/sys/module/firmware_class/')
# Block devices by mmcblk number: the number follows probe order and is not always the eMMC's (FINDINGS 31l). The
# card's partitions (Android's mmcblk1p*, for vold) are left alone - no vendor daemon run here opens the card, and a
# system-group card would be the first user's on Linux (where the root filesystem may be on it). The vendor daemons
# reach the eMMC through by-name, linked to the eMMC only; its RPMB partition is the only one there is (a card has
# none), so mmcblk0rpmb becomes any number's.
BY_NUMBER = ('/dev/block/mmcblk', '/dev/mmcblk')

# The Linux ids, looked up when the script runs: in /etc itself (no getent: the initramfs and OpenWrt have no NSS,
# and the accounts here are local system accounts).
HEADER = r'''# The vendor daemons' account (android-run starts them as it); without one (OpenWrt, the initramfs) the uid and gid
# 1000 they switch to themselves. MU300_VENDOR_USER: the account android-run says it starts them as (android-vendor-
# start sets it; empty: none, uid 1000 even where mu300vnd exists - issue #314). The GPU's group: video, else root.
id_of() {  # id_of NAME FILE: the id (third field) of NAME in FILE (passwd or group format), nothing if none
    [ -r "$2" ] || return 0
    while IFS=: read -r n _ i _ || [ -n "$n" ]; do [ "$n" = "$1" ] && { echo "$i"; return 0; }; done < "$2"
    return 0
}
E=${MU300_ETC:-/etc}
VU=${MU300_VENDOR_USER-mu300vnd}
V=; VG=
[ -z "$VU" ] || { V=$(id_of "$VU" "$E/passwd"); VG=$(id_of "$VU" "$E/group"); }
V=${V:-1000}; VG=${VG:-1000}
GPU=$(id_of video "$E/group"); GPU=${GPU:-0}'''


def root_only(path):
    return path.startswith(ROOT_ONLY)


def node(path):
    if path.startswith(BY_NUMBER):
        return path.replace('mmcblk0rpmb', 'mmcblk*rpmb') if path.endswith('mmcblk0rpmb') else None
    return path


def ids(user, group, m=None):
    """(owner, group) on Linux of Android's USER:GROUP; M the node's Android mode (ueventd), None for a chown alone"""
    if group == 'graphics' and m is not None and int(m, 8) & 0o006:
        return '0', '$GPU'                                   # the GPU's nodes: root:video, not everyone's
    return MAP.get(user, ('0', '0'))[0], MAP.get(group, ('0', '0'))[1]


def mode(m, path, owner=None, group=None):
    m = int(m, 8) & 0o7777
    m &= ~0o007 if path.startswith('/dev/') else ~0o002     # nothing for others on a node; never written by anyone
    if owner == '0' and group == '0':
        m &= ~0o070                                          # root's alone: no group bits for the root group
    return f'{m:04o}'


out = ['#!/bin/sh', '# generated by gen-ueventd-perms.py: Android device node ownership on plain Linux', HEADER]
ueventd, rcs = sys.argv[1], sys.argv[2:]
for line in open(ueventd):
    f = line.split('#')[0].split()
    if len(f) >= 4 and f[0].startswith('/dev/') and f[2] in AID and f[3] in AID and node(f[0]):
        if root_only(f[0]):
            out.append(f'for n in {node(f[0])}; do [ -e "$n" ] && chown 0:0 "$n" && chmod 0600 "$n"; done')
            continue
        u, g = ids(f[2], f[3], f[1])
        u = '0' if f[0].startswith(ROOT_OWNED) else u
        out.append(f'for n in {node(f[0])}; do [ -e "$n" ] && chown {u}:{g} "$n" && chmod {mode(f[1], f[0], u, g)} "$n"; done')
out.append('# chown/chmod from vendor init *.rc (Android init applies these after ueventd)')
for rc in rcs:
    for line in open(rc):
        f = line.split('#')[0].split()
        if len(f) < 3 or f[0] not in ('chown', 'chmod') or not f[-1].startswith(('/dev/', '/sys/', '/proc/')):
            continue
        if root_only(f[-1]):
            if f[0] == 'chown':
                out.append(f'for n in {f[-1]}; do [ -e "$n" ] && chown 0:0 "$n" && chmod 0600 "$n"; done')
            continue
        if f[0] == 'chmod':
            out.append(f'for n in {f[-1]}; do [ -e "$n" ] && chmod {mode(f[1], f[-1])} "$n"; done')
        elif f[1] in AID and (len(f) == 3 or f[2] in AID):
            u, g = ids(f[1], f[2] if len(f) == 4 else f[1])
            out.append(f'for n in {f[-1]}; do [ -e "$n" ] && chown {u}:{g} "$n"; done')
out.append('# from Android system init.rc: wakelocks are written by uid system')
out.append('for n in /sys/power/wake_lock /sys/power/wake_unlock; do [ -e "$n" ] && chown $V:$VG "$n" && chmod 0660 "$n"; done')
out.append('true')
print('\n'.join(out))
