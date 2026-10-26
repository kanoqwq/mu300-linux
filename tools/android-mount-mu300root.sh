#!/system/bin/sh
# Attach the mu300root ext4 (free eMMC region) on Android via a bounded loop device and mount it.
# usage: android-mount-mu300root.sh <mountpoint>   |   android-mount-mu300root.sh -u <mountpoint>
#        MU300_SD_DEV=/dev/block/mmcblk1p1 android-mount-mu300root.sh <mountpoint>   (the mu300sd ext4 on the SD card)
set -e
# region of the free eMMC space after the last GPT partition (install.sh passes the values it computed)
OFF=${MU300_OFF:-27762098176}
# Android's shell does 32-bit arithmetic: byte offsets of this region overflow there, so the size is never
# computed here. Callers that know it pass MU300_SIZE (install.sh, tools/reset-password.sh); without it the loop
# simply spans to the end of the disk, which mounts any size of filesystem.
SIZE=${MU300_SIZE:-}
# MU300_RO=1: read only, and no journal replay either (an ext4 "ro" mount still replays it, which writes): the
# Magisk installer only looks at what is installed before its plan, and a dry run must not change the filesystem
# MU300_RO=recover: read only after the journal is replayed (what any mount without noload does, Linux's at boot too):
# a Linux that went down without unmounting (OpenWrt's reboot does: needs_recovery stays set) leaves its last changes
# in the journal, and a noload view shows the files from before them - an /etc/shadow with the password before the
# last passwd (needs_recovery was set on an F50, FINDINGS 126). The password check of an update uses this.
MOUNT_OPTS=noatime
[ "${MU300_RO:-}" != 1 ] || MOUNT_OPTS=ro,noload
[ "${MU300_RO:-}" != recover ] || MOUNT_OPTS=ro

if [ "$1" = -u ]; then
    L=$(grep " $2 " "${MU300_MOUNTS:-/proc/mounts}" | cut -d' ' -f1)
    umount "$2"
    # only a loop is ours to detach: the SD card is a block device of its own. Android's umount (toybox) frees the
    # loop of what it unmounts by itself, and a losetup -d after that fails: only a loop still attached is detached.
    case "$L" in
        */loop*) [ ! -e "${MU300_SYSFS:-/sys}/block/${L##*/}/loop/backing_file" ] || losetup -d "$L" ;;
    esac
    echo UNMOUNTED; exit 0
fi
# the SD card: a block device of its own, no loop. Same checks as for the region, for the card's label.
if [ -n "${MU300_SD_DEV:-}" ]; then
    grep -q "^$MU300_SD_DEV " /proc/mounts && { echo "ALREADY-MOUNTED $MU300_SD_DEV"; exit 1; }
    magic=$(dd if="$MU300_SD_DEV" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')
    [ "$magic" = 53ef ] || { echo "NO-EXT4 magic=$magic"; exit 1; }
    label=$(dd if="$MU300_SD_DEV" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000')
    [ "$label" = mu300sd ] || { echo "NOT-MU300SD label=$label"; exit 1; }
    mkdir -p "$1"
    mount -t ext4 -o "$MOUNT_OPTS" "$MU300_SD_DEV" "$1"
    echo "MOUNTED $MU300_SD_DEV on $1"
    exit 0
fi
MP=$1
# never attach the same ext4 twice (two loops mounting one filesystem corrupts it); a leftover loop from an
# interrupted run is reused when nothing has it mounted
REUSE=
for o in /sys/block/loop*/loop/offset; do
    [ "$(cat "$o" 2>/dev/null)" = "$OFF" ] || continue
    b=${o%/loop/offset}; b=${b##*/}
    if grep -q "^/dev/block/$b " /proc/mounts; then echo "ALREADY-MOUNTED $b"; exit 1; fi
    REUSE=/dev/block/$b
done
if [ -n "$REUSE" ]; then
    L=$REUSE
    mkdir -p "$MP"
    mount -t ext4 -o "$MOUNT_OPTS" "$L" "$MP"
    echo "MOUNTED $L on $MP"
    exit 0
fi
f=$(losetup -f 2>&1 | grep -o '/dev/block/loop[0-9]*' | head -1)
n=${f##*loop}
[ -n "$n" ] || { echo NO-LOOP-INDEX; exit 1; }
[ -e /sys/block/loop$n/dev ] || { echo NO-SYSFS-loop$n; exit 1; }
L=/dev/block/loop$n
# ueventd may create the node concurrently; only fail if it still does not exist
[ -b "$L" ] || mknod "$L" b $(cut -d: -f1 /sys/block/loop$n/dev) $(cut -d: -f2 /sys/block/loop$n/dev) 2>/dev/null || [ -b "$L" ]
[ ! -e /sys/block/loop$n/loop/backing_file ] || { echo LOOP-BUSY-$L; exit 1; }
if [ -n "$SIZE" ]; then
    losetup -o $OFF -S $SIZE "$L" /dev/block/mmcblk0
else
    losetup -o $OFF "$L" /dev/block/mmcblk0
fi
[ "$(cat /sys/block/loop$n/loop/offset)" = "$OFF" ] && [ "$(cat /sys/block/loop$n/loop/backing_file)" = /dev/block/mmcblk0 ] || { echo LOOP-MISMATCH; losetup -d "$L"; exit 1; }
[ -z "$SIZE" ] || [ "$(blockdev --getsize64 $L)" = "$SIZE" ] || { echo LOOP-SIZE-MISMATCH; losetup -d "$L"; exit 1; }
# ext4 superblock at 1024: s_magic @+56 (0xEF53), s_volume_name @+120 (16 bytes)
magic=$(dd if="$L" bs=1 skip=1080 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')
[ "$magic" = 53ef ] || { echo "NO-EXT4 magic=$magic"; losetup -d "$L"; exit 1; }
label=$(dd if="$L" bs=1 skip=1144 count=16 2>/dev/null | tr -d '\000')
[ "$label" = mu300root ] || { echo "NOT-MU300ROOT label=$label"; losetup -d "$L"; exit 1; }
mkdir -p "$MP"
mount -t ext4 -o "$MOUNT_OPTS" "$L" "$MP"
echo "MOUNTED $L on $MP"
